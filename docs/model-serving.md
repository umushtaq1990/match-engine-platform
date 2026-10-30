# Shared Model Serving — LLM & Embeddings (Phase 15)

Status: v1 — **implemented and verified end-to-end** (service running, both models pulled, real
inference confirmed). No consumers wired up yet — that's Use Case C/F work, still future. This
document was written and reviewed before any code existed, the same discipline as Phase 12
(Kubernetes) and Phase 14 (behavioral analytics). The design below matched the build with no
changes required.

## Why this is a shared service, not something each consumer loads itself

A 1–3B parameter model costs real, fixed RAM/VRAM the moment it's loaded — independent of how
often it's called. If the matching API, a future skill-extraction service, and anything else that
wants inference each loaded their own copy, that fixed cost is paid once per service, not once
total. On this project's measured hardware (16GB RAM, ~7.4GB allocated to the Docker Desktop VM —
`docs/hardware-inventory.md`), that multiplication is the difference between "comfortably fits"
and "doesn't."

So model inference becomes its own bounded context, the same separation-of-concerns principle
already applied twice in this project (Phase 2's ingestion/migrate/api split, Phase 14's analytics
service): one shared service holds the model(s) in memory, every consumer calls it over HTTP, and
no consumer links against a model-serving library directly.

## Serving tool: Ollama

Chosen over llama.cpp-server (lower-level, more manual quantization/model management, no clear
benefit at this scale) and vLLM/text-generation-inference (production-shaped for concurrent
multi-user GPU throughput — real overkill for a single-user local validation step; the natural
upgrade path once this moves past "try things out").

Ollama is used as an **official, unmodified image** (`ollama/ollama`) — no custom Dockerfile, same
pattern already used for Postgres/Prometheus/cAdvisor/Grafana in this project. One process serves
**both** models this phase needs via its own REST API:
- `POST /api/generate` / `POST /api/chat` — the LLM
- `POST /api/embeddings` — the embedding model

One service, one image, two models — not two separate serving containers. Splitting them is a
documented future step (see "Phased roadmap"), not needed to prove the approach works.

## Models (v1)

| Role | Model | Size (quantized) | Why this one |
|---|---|---|---|
| LLM | `llama3.2:1b` | ~1.3GB | Smallest credible instruct model with real reasoning ability; CPU-inference speed at this size is fast enough to validate an approach without GPU passthrough |
| Embeddings | `nomic-embed-text` | ~274MB | Purpose-built embedding model (not a repurposed LLM), small, CPU-friendly, widely used as a quality baseline |

Both are placeholders to start with, not a permanent commitment — swapping either is a one-line
config change (a different `ollama pull` target), never a consumer code change, because consumers
only ever talk to Ollama's stable API, never to the model directly.

## Resource footprint vs. measured hardware

~1.6GB combined model weights, well inside the ~7.4GB Docker VM allocation that already runs
Postgres + api + analytics + mlflow + prometheus + cadvisor + grafana comfortably (`docs/
hardware-inventory.md` — that stack has never come close to the 16GB host ceiling). CPU-only
inference for 1–3B-class models is adequate for *validating* the approach; GPU passthrough is a
later step if throughput, not correctness, becomes the question.

## Service boundary

| | Decision | Rationale |
|---|---|---|
| Service | `ollama` in `docker-compose.yml`, official image, no custom Dockerfile | No custom build needed — same precedent as `db`/`prometheus`/`cadvisor`/`grafana` |
| Model provisioning | One-off `profiles: tools` job (`ollama-pull`), run explicitly: `docker compose run --rm ollama-pull` | Same shape as `migrate`/`seed`/`dataquality`/`analytics-seed` — never baked into the image itself, so swapping models doesn't mean rebuilding anything |
| Persistence | Named volume for Ollama's model storage | Avoids re-pulling ~1.6GB on every container recreate |
| Auth | None for v1 — internal-only, not exposed outside the compose network | No consumer outside this platform calls it directly; revisit if that stops being true |

## Consumer pattern

Same shape as the Phase 14 instrumentation hook (`app/api/analytics_client.py`): a synchronous
HTTP call, a short timeout, and explicit handling of "the model service is unavailable" — **not**
silently swallowed the way analytics impressions are, since here the caller usually *needs* the
result to do its job (e.g., Use Case C has nothing to return without an extraction result). The
right failure mode is a clear error back to the caller, not a best-effort no-op — a real
difference from Phase 14's design, worth calling out rather than copying blindly.

## What this unblocks

- **Use Case C (free-text skill extraction)** — per assumption A5b, this use case specifically
  skips the rule-based baseline and goes straight to embedding/NLP-based extraction. This is the
  serving layer that work has been waiting on.
- **Use Case F (similarity/semantic search)** — never scheduled; embeddings for jobs/candidates/
  courses would be generated via this same service and compared with cosine similarity (a vector
  index like Qdrant is a separate, later decision — not needed to prove embeddings work at this
  dataset's scale; a brute-force comparison over ~1,000–2,000 rows is fast enough to start with).
- **Phase 14 v3 (ML clustering)** — if engagement-feature clustering ever needs richer features
  than raw counts, embeddings are available from the same service. Not a current plan, just an
  option this unlocks.

Neither Use Case C nor F is being built in this phase — this document only stands up the serving
layer both will eventually call.

## Phased roadmap

- **v1 (this phase)**: Ollama service, both models pulled and verified reachable with real
  prompts/embedding calls over the compose network. No consumer wired up yet.
- **v2**: Use Case C built against this service (extraction + the embedding-based matching A5b
  already committed to).
- **v3**: Use Case F built (similarity search), likely introducing a proper vector index once
  brute-force comparison stops being fast enough — measured, not assumed.
- **v4, only if throughput becomes the actual bottleneck**: GPU passthrough into the Ollama
  container, larger model variants, or splitting LLM/embedding serving into separate containers
  for independent scaling.

## Verified against real execution (not fabricated)

- **Image**: pinned `ollama/ollama:0.40.13` (confirmed against Docker Hub's API directly, not
  guessed — `latest` is not a reliable pin, consistent with every other image in this project
  being version-pinned).
- **Both models pulled** via `docker compose run --rm ollama-pull`: `llama3.2:1b` (1.3GB),
  `nomic-embed-text:latest` (274MB) — confirmed with `ollama list` inside the running container,
  not just a successful exit code.
- **Real LLM generation**, not a mocked response: `POST /api/generate` with a real prompt
  ("what does a job matching engine do?") returned a correct, coherent, on-topic answer. Cold
  start (model load into RAM) took ~45s for 26 tokens; a second, warm call completed in ~2.6s for
  39 tokens — the cold-start cost is paid once per model per container lifetime, not per request.
- **Real embeddings**, not a stub: `POST /api/embeddings` against `nomic-embed-text` returned an
  actual 768-dimension float vector (confirmed length and real non-zero values, not a placeholder
  array). Cold call ~11s, warm call ~1.2s.
- **Memory footprint measured, not estimated**: `docker stats` showed the `ollama` container using
  ~2.56GiB with both models loaded (one of them mid-inference) — comfortably inside the ~7.41GiB
  Docker VM allocation this project has already been running 7 other services inside
  (`docs/hardware-inventory.md`), confirming the design doc's footprint estimate held in practice,
  not just on paper.
- **CI**: `fresh-volume-smoke-test` now starts `ollama` from nothing, runs the real pull job,
  confirms both models are present, and makes one real generate call and one real embeddings call
  against them — the same "prove it from zero, every time" discipline as every other service in
  this platform.

## Known limitations (v1, by design)

- CPU-only — inference latency has not yet been measured against any real use case's needs (there
  isn't one yet; both consumers are future work).
- No auth, no rate limiting on the Ollama service itself — acceptable only because it's unreachable
  from outside the compose network, same trust-boundary reasoning as the rest of this platform's
  internal services.
- Single Ollama instance, no replica/HA story — a reasonable v1 limitation for a one-developer
  local platform, not something a real multi-tenant deployment could accept unchanged.
- No observability yet (no Prometheus scrape target) — deferred until there's an actual consumer
  generating traffic worth measuring.
