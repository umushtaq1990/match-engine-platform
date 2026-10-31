# Shared Model Serving — LLM & Embeddings

Status: v1 shared service — **implemented and verified end-to-end**; now has real consumers.
Originally stood up with no consumers wired up (this document was written and reviewed before any
code existed); skill extraction (`docs/skill-extraction.md`) and embeddings-based matching
(`docs/embedding-matching.md`) now call it directly, and the model lineup evolved from the original
v1 pair based on measured comparisons documented in those two docs (qwen2.5:1.5b replaced
`llama3.2:1b`; `all-minilm` was added as the production embedding model alongside
`nomic-embed-text`).

## Purpose & Scope

A single shared inference service for every consumer that needs an LLM or embedding call, rather
than each consumer loading its own model copy. In scope: the serving layer itself (Ollama, model
provisioning, resource footprint). Out of scope: what calls it — that's each consumer's own doc.

## Architecture & Design

**Why a shared service, not something each consumer loads itself**: a 1–3B parameter model costs
real, fixed RAM/VRAM the moment it's loaded — independent of how often it's called. If the matching
API, a skill-extraction service, and anything else that wants inference each loaded their own copy,
that fixed cost is paid once per service, not once total. On this project's measured hardware
(16GB RAM, ~7.4GB allocated to the Docker Desktop VM — `docs/infrastructure-sizing.md`), that
multiplication is the difference between "comfortably fits" and "doesn't." So model inference
becomes its own bounded context, the same separation-of-concerns principle applied elsewhere in
this project (the ingestion/migrate/api split, the analytics service split): one shared service
holds the model(s) in memory, every consumer calls it over HTTP, and no consumer links against a
model-serving library directly.

**Serving tool: Ollama**, chosen over llama.cpp-server (lower-level, more manual quantization/model
management, no clear benefit at this scale) and vLLM/text-generation-inference (production-shaped
for concurrent multi-user GPU throughput — real overkill for a single-user local validation step;
the natural upgrade path once this moves past "try things out"). Used as an **official, unmodified
image** (`ollama/ollama`) — no custom Dockerfile, same pattern used for Postgres/Prometheus/
cAdvisor/Grafana in this project. One process serves every model needed via its own REST API
(`POST /api/generate`/`/api/chat` for the LLM, `POST /api/embeddings` for embedding models) — one
service, one image, multiple models, not one serving container per model.

**Models**: originally `llama3.2:1b` (LLM) + `nomic-embed-text` (embeddings) as placeholders, both
swappable via a one-line `ollama pull` config change, never a consumer code change (consumers only
ever talk to Ollama's stable API, never to the model directly). `llama3.2:1b` was later replaced by
`qwen2.5:1.5b` after a measured comparison (F1 0.664 vs. 0.504 on the same extraction task — see
`docs/skill-extraction.md`); `all-minilm` was added as the production embedding model after a
separate measured comparison against `nomic-embed-text` found it separated near-miss skill pairs
more cleanly (see `docs/embedding-matching.md`).

**Resource footprint vs. measured hardware**: original two-model combination was ~1.6GB combined
weights, well inside the Docker VM allocation that already ran the rest of the stack comfortably.
See `docs/infrastructure-sizing.md` for the current full-stack measurement (Ollama with two models
loaded mid-inference: ~2.56GiB).

**Service boundary**:

| | Decision | Rationale |
|---|---|---|
| Service | `ollama` in `docker-compose.yml`, official image, no custom Dockerfile | No custom build needed — same precedent as `db`/`prometheus`/`cadvisor`/`grafana` |
| Model provisioning | One-off `profiles: tools` job (`ollama-pull`), run explicitly: `docker compose run --rm ollama-pull` | Same shape as `migrate`/`seed`/`dataquality`/`analytics-seed` — never baked into the image itself, so swapping models doesn't mean rebuilding anything |
| Persistence | Named volume for Ollama's model storage | Avoids re-pulling model weights on every container recreate |
| Auth | None — internal-only, not exposed outside the compose network | No consumer outside this platform calls it directly; revisit if that stops being true |

**Consumer pattern**: a synchronous HTTP call, a short timeout, and explicit handling of "the
model service is unavailable" — **not** silently swallowed the way analytics impressions are
(`app/api/analytics_client.py`), since here the caller usually *needs* the result to do its job
(extraction has nothing to return without one). The right failure mode is a clear error back to the
caller, not a best-effort no-op — deliberately different from the analytics hook's design, not an
inconsistency.

## Implementation

`ollama` is an always-on service like `api`/`analytics` — reachable for the lifetime of the stack,
not spun up per request. Model weights live in a named Docker volume, provisioned by the one-off
`ollama-pull` job rather than baked into the image. Consumers (`extraction`,
`embeddings-compute`) reach it over the compose network at `http://ollama:11434`.

## Verification & Results

**Original v1 stand-up, verified before any consumer existed**:
- Image pinned `ollama/ollama:0.40.13` (confirmed against Docker Hub's API directly, not guessed —
  `latest` is not a reliable pin, consistent with every other image in this project being
  version-pinned).
- Both original models pulled via `docker compose run --rm ollama-pull`: `llama3.2:1b` (1.3GB),
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
  ~2.56GiB with both models loaded (one of them mid-inference) — comfortably inside the Docker VM
  allocation this project has already been running the rest of the stack inside, confirming the
  design doc's footprint estimate held in practice, not just on paper.
- CI's `fresh-volume-smoke-test` starts `ollama` from nothing, runs the real pull job, confirms
  models are present, and makes real generate/embeddings calls against them — the same "prove it
  from zero, every time" discipline as every other service in this platform.

**Since then**: the model lineup changed (qwen2.5:1.5b, all-minilm added) and two real consumers
now exist — see `docs/skill-extraction.md` and `docs/embedding-matching.md` for their own
measured verification against this serving layer, including a transient `httpx.ReadError` traced
to the host machine sleeping mid-request (not an Ollama bug), resolved by retry with zero lost
progress thanks to the incremental embeddings design.

## Known Limitations

- CPU-only — inference latency is measured for the two real consumers that exist today
  (`docs/skill-extraction.md`, `docs/embedding-matching.md`), not a generic "it's fast enough"
  claim.
- No auth, no rate limiting on the Ollama service itself — acceptable only because it's unreachable
  from outside the compose network, same trust-boundary reasoning as the rest of this platform's
  internal services.
- Single Ollama instance, no replica/HA story — a reasonable limitation for a one-developer local
  platform, not something a real multi-tenant deployment could accept unchanged.
- No observability (no Prometheus scrape target) for Ollama itself — not added since the two real
  consumers' own request-level metrics (via their own `/metrics`) are what's actually monitored.

## Related Documents

- `docs/architecture.md` — overview and building block view
- `docs/progress.md` — full build history
- `docs/skill-extraction.md` — Use Case C, the first real consumer
- `docs/embedding-matching.md` — Use Case F groundwork, the second real consumer, including the
  model comparison that added `all-minilm`
- `docs/infrastructure-sizing.md` — current full-stack resource measurement
