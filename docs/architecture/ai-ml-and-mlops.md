# AI/ML Serving and MLOps (Ollama, MLflow, Evaluation)

Status: v1 shared service, real consumers wired in; evaluation harness at
full-population baseline.

Level-3 diagram (cropped from the master Technical Architecture diagram):
[`ai-ml-and-mlops.drawio`](ai-ml-and-mlops.drawio).

This covers the two things that make "add machine learning, but only when it's
earned" possible: a single shared place to run models (Ollama), and a single shared
way to measure whether a model-based change actually helped (the evaluation
harness + MLflow). Two previously separate docs (`model-serving.md`,
`evaluation-strategy.md`) are merged here because together they are this project's
MLOps layer — one serves, one measures.

## Part 1 — Shared Model Serving (LLM & Embeddings)

### Purpose & Scope

A single shared inference service for every consumer that needs an LLM or embedding
call, rather than each consumer loading its own model copy. In scope: the serving
layer itself (Ollama, model provisioning, resource footprint). Out of scope: what
calls it — that's each consumer's own doc
([application-services/skill-extraction.md](application-services/skill-extraction.md),
[data-platform/embedding-matching.md](data-platform/embedding-matching.md)).

### Architecture & Design

Why a shared service, not something each consumer loads itself: a 1-3B parameter
model costs real, fixed RAM/VRAM the moment it's loaded, independent of how often
it's called. If the matching API, a skill-extraction service, and anything else that
wants inference each loaded their own copy, that fixed cost is paid once per service,
not once total. On this project's measured hardware (16GB RAM, about 7.4GB allocated
to the Docker Desktop VM — [operations/infrastructure-sizing.md](../operations/infrastructure-sizing.md)),
that multiplication is the difference between "comfortably fits" and "doesn't." So
model inference is its own bounded context, the same separation-of-concerns
principle applied elsewhere in this project: one shared service holds the model(s)
in memory, every consumer calls it over HTTP, and no consumer links against a
model-serving library directly.

Serving tool: Ollama, chosen over llama.cpp-server (lower-level, more manual
quantization/model management, no clear benefit at this scale) and vLLM/text-
generation-inference (production-shaped for concurrent multi-user GPU throughput,
real overkill for a single-user local validation step; the natural upgrade path once
this moves past "try things out"). Used as an official, unmodified image
(`ollama/ollama`), no custom Dockerfile, same pattern used for Postgres/Prometheus/
cAdvisor/Grafana in this project. One process serves every model needed via its own
REST API (`POST /api/generate`/`/api/chat` for the LLM, `POST /api/embeddings` for
embedding models) — one service, one image, multiple models, not one serving
container per model.

Models: originally `llama3.2:1b` (LLM) plus `nomic-embed-text` (embeddings) as
placeholders, both swappable via a one-line `ollama pull` config change, never a
consumer code change (consumers only ever talk to Ollama's stable API, never to the
model directly). `llama3.2:1b` was later replaced by `qwen2.5:1.5b` after a measured
comparison (F1 0.664 vs. 0.504 on the same extraction task — see
[application-services/skill-extraction.md](application-services/skill-extraction.md));
`all-minilm` was added as the production embedding model after a separate measured
comparison against `nomic-embed-text` found it separated near-miss skill pairs more
cleanly (see [data-platform/embedding-matching.md](data-platform/embedding-matching.md)).

Resource footprint vs. measured hardware: the original two-model combination was
about 1.6GB combined weights, well inside the Docker VM allocation that already ran
the rest of the stack comfortably. See
[operations/infrastructure-sizing.md](../operations/infrastructure-sizing.md) for
the current full-stack measurement (Ollama with two models loaded mid-inference:
about 2.56GiB).

Service boundary:

| | Decision | Rationale |
|---|---|---|
| Service | `ollama` in `docker-compose.yml`, official image, no custom Dockerfile | No custom build needed, same precedent as `db`/`prometheus`/`cadvisor`/`grafana` |
| Model provisioning | One-off `profiles: tools` job (`ollama-pull`), run explicitly: `docker compose run --rm ollama-pull` | Same shape as `migrate`/`seed`/`dataquality`/`analytics-seed`, never baked into the image itself, so swapping models doesn't mean rebuilding anything |
| Persistence | Named volume for Ollama's model storage | Avoids re-pulling model weights on every container recreate |
| Auth | None, internal-only, not exposed outside the compose network | No consumer outside this platform calls it directly; revisit if that stops being true |

Consumer pattern: a synchronous HTTP call, a short timeout, and explicit handling of
"the model service is unavailable," not silently swallowed the way analytics
impressions are (`app/api/analytics_client.py`), since here the caller usually needs
the result to do its job (extraction has nothing to return without one). The right
failure mode is a clear error back to the caller, not a best-effort no-op,
deliberately different from the analytics hook's design, not an inconsistency.

### Implementation

`ollama` is an always-on service like `api`/`analytics`, reachable for the lifetime
of the stack, not spun up per request. Model weights live in a named Docker volume,
provisioned by the one-off `ollama-pull` job rather than baked into the image.
Consumers (`extraction`, `embeddings-compute`) reach it over the compose network at
`http://ollama:11434`.

### Verification & Results

Original v1 stand-up, verified before any consumer existed:

- Image pinned `ollama/ollama:0.40.13` (confirmed against Docker Hub's API directly,
  not guessed — `latest` is not a reliable pin, consistent with every other image in
  this project being version-pinned).
- Both original models pulled via `docker compose run --rm ollama-pull`:
  `llama3.2:1b` (1.3GB), `nomic-embed-text:latest` (274MB), confirmed with `ollama
  list` inside the running container, not just a successful exit code.
- Real LLM generation, not a mocked response: `POST /api/generate` with a real
  prompt ("what does a job matching engine do?") returned a correct, coherent,
  on-topic answer. Cold start (model load into RAM) took about 45s for 26 tokens; a
  second, warm call completed in about 2.6s for 39 tokens, the cold-start cost is
  paid once per model per container lifetime, not per request.
- Real embeddings, not a stub: `POST /api/embeddings` against `nomic-embed-text`
  returned an actual 768-dimension float vector (confirmed length and real
  non-zero values, not a placeholder array). Cold call about 11s, warm call about
  1.2s.
- Memory footprint measured, not estimated: `docker stats` showed the `ollama`
  container using about 2.56GiB with both models loaded (one of them mid-inference),
  comfortably inside the Docker VM allocation this project has already been running
  the rest of the stack inside, confirming the design doc's footprint estimate held
  in practice, not just on paper.
- CI's `fresh-volume-smoke-test` starts `ollama` from nothing, runs the real pull
  job, confirms models are present, and makes real generate/embeddings calls against
  them, the same "prove it from zero, every time" discipline as every other service
  in this platform.

Since then: the model lineup changed (`qwen2.5:1.5b`, `all-minilm` added) and two
real consumers now exist — see
[application-services/skill-extraction.md](application-services/skill-extraction.md)
and
[data-platform/embedding-matching.md](data-platform/embedding-matching.md)
for their own measured verification against this serving layer, including a
transient `httpx.ReadError` traced to the host machine sleeping mid-request (not an
Ollama bug), resolved by retry with zero lost progress thanks to the incremental
embeddings design.

### Known Limitations

- CPU-only — inference latency is measured for the two real consumers that exist
  today, not a generic "it's fast enough" claim.
- No auth, no rate limiting on the Ollama service itself, acceptable only because
  it's unreachable from outside the compose network, same trust-boundary reasoning
  as the rest of this platform's internal services.
- Single Ollama instance, no replica/HA story, a reasonable limitation for a
  one-developer local platform, not something a real multi-tenant deployment could
  accept unchanged.
- No observability (no Prometheus scrape target) for Ollama itself, not added
  since the two real consumers' own request-level metrics (via their own
  `/metrics`) are what's actually monitored.

## Part 2 — Evaluation Strategy

### Purpose & Scope

Per the project's own rule: no ML approach (embeddings, LLM-based extraction) should
be adopted without a way to measure whether it's actually better than the
deterministic baseline. This subsystem builds that measurement capability, a direct
prerequisite for the Use Case C skill-extraction work and for the embedding-matching
comparison ([data-platform/embedding-matching.md](data-platform/embedding-matching.md)).

Out of scope: comparing alternative scoring-weight configurations against each other
— the harness supports it, but no comparison run has happened yet (explicitly
deferred, user's call, until other architecture work finished first).

### Architecture & Design

Avoiding circular evaluation: the central design risk in any synthetic evaluation —
if "relevant" is defined using the same formula being tested, the evaluation proves
nothing, the system would score perfectly by construction.
`app/evaluation/ground_truth.py` deliberately avoids this: ground truth is defined
using only occupation alignment (`Candidate.primary_occupation_id ==
Job.occupation_id`), while the scoring formula being evaluated
(`app/matching/scoring.py`) blends skill, location, salary, and occupation. Measuring
the full blended ranking against an occupation-only ground truth is meaningful
precisely because they're not the same computation.

This is still a synthetic, coarse definition — a real recruiter's notion of
"relevant" accounts for skill gaps, which this ground truth deliberately ignores
(that's what `skill_gap.py` analyzes separately). Per the brief: this does not prove
real-world effectiveness, only internal consistency of the ranking logic against a
documented, defensible synthetic label.

Metrics implemented: standard binary-relevance IR metrics (`app/evaluation/metrics.py`,
pure functions, 14 unit tests): Precision@K, Recall@K, NDCG@K, Mean Reciprocal Rank
(MRR, averaged across candidates).

Results storage, split by purpose, not duplicated across two systems tracking the
same thing:

- MLflow (`docker/Dockerfile.mlflow`, always-on service at `http://localhost:5000`,
  backend store: a dedicated `mlflow` Postgres database on the same instance) tracks
  run-level params (scoring weights, k, sample size) and aggregate metrics, a
  browsable, comparable experiment history across scoring-weight configs, exactly
  the "show what was tried" capability this needed.
- Postgres (`evaluation_runs`/`evaluation_candidate_results`, same two-level pattern
  as `data_quality_runs`) keeps the per-candidate detail rows, for drill-down
  analysis and a future dashboard, which queries Postgres more naturally than
  MLflow's own store.

MLflow logging is best-effort: `run_evaluation.py` always persists to Postgres
first; if the MLflow server is unreachable, the run still completes and only the
MLflow log is skipped (with a warning), not treated as a fatal error.

### Implementation

Sample size, measured, not guessed. The first measurement: about 1.95s per
candidate (5-candidate timing test), consistent with an undiagnosed N+1 query
limitation in `app/matching/profiles.py` at the time. Evaluating the full 1,899
eligible candidates would have taken over an hour, so evaluation initially ran
against a sample of 50 instead.

Once the N+1 limitation was fixed (batch-loading instead of one query per row, see
[application-services/matching-engine.md](application-services/matching-engine.md)),
per-candidate latency dropped to about 0.06s. Full-population evaluation (all 1,899
eligible candidates) now completes in about 2m25s and is the default
(`DEFAULT_SAMPLE_SIZE` in `run_evaluation.py`); the original 50-candidate cap is no
longer necessary.

### Verification & Results

A real bug caught and fixed while wiring this up. First run against the live MLflow
server crashed with `UnicodeEncodeError`, MLflow prints a confirmation emoji to
stdout after a run finishes, which this Windows console (cp1252) can't encode. The
crash happened during `mlflow.end_run()`, before the run was marked `FINISHED` in
MLflow's backend, leaving a stuck `RUNNING` run behind even though the
params/metrics had already been logged successfully (verified via MLflow's REST API
before concluding this). Fixed using MLflow's own supported suppression flag
(`MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT`), not a workaround; the stuck run was
deleted via the MLflow API once the fix was confirmed on a clean re-run.

Baseline results across three measurement rounds:

| Metric | Round 1 (sample of 50) | Round 2 (full population, 1,899) | Round 3 (full population, domain-anchored data) |
|---|---|---|---|
| k | 10 | 10 | 10 |
| Config | `skill=0.55, location=0.15, salary=0.15, occupation=0.15` | same | same |
| Precision@10 | 0.47 | 0.4878 | 0.4964 |
| Recall@10 | 0.7458 | 0.7625 | 0.7587 |
| NDCG@10 | 0.7283 | 0.7461 | 0.7352 |
| MRR | 0.8029 | 0.8032 | 0.7975 |

The full-population numbers (Round 2) are close to the 50-candidate sample (Round 1,
largest gap: Precision@10, 0.47 vs. 0.4878), a useful confirmation that the original
sample, while small, wasn't misleading. The full run is still the one future
weight-comparison work should use, now that it's cheap enough to be the default
rather than a documented compromise.

Round 3 update: the synthetic generator later changed
([data-platform/data-at-scale.md](data-platform/data-at-scale.md), occupation skill
profiles are now anchored to a coherent "domain" instead of sampled uniformly at
random, to give the knowledge graph real clustering signal). Re-ran this exact
evaluation against the new data: ranking quality stayed essentially stable
(differences within normal run-to-run noise), which is the expected, reassuring
result, the occupation-alignment ground truth itself didn't change, only which
skills co-occur within an occupation did. The real quality shift from this data
change shows up in the embedding-based evaluation instead, not here, see
[data-platform/embedding-matching.md](data-platform/embedding-matching.md).

Reading these honestly: Recall@10 of about 0.76 and MRR of about 0.80 indicate the
ranking generally surfaces occupation-aligned jobs near the top, expected, since
occupation is one of the four scored components. Precision@10 of about 0.49 means
on average about half of the top-10 recommended jobs are occupation-aligned; the
rest are jobs the system ranked highly on skill/location/salary despite a different
occupation. Whether that's desirable (surfacing adjacent/transferable opportunities)
or a weakness (diluting relevance) is exactly the kind of question this harness is
built to let us investigate by comparing alternative weight configurations, still
not resolved here, since no comparison has been run yet (see Known Limitations).

### Known Limitations

- Ground truth is occupation-only, ignores skill gaps and salary/location fit
  entirely. A candidate could be marked "relevant" for a job they're a poor fit for
  on every other dimension.
- No test coverage yet for `ground_truth.py`/`run_evaluation.py` themselves beyond
  manual verification against the real database (consistent with how
  `data_quality.py` and `rank.py` were verified, pure logic gets unit tests,
  DB-touching orchestration gets verified by inspecting real output, not a DB test
  fixture).
- No scoring-weight comparison has been run yet, explicitly deferred (user's call)
  until other architecture work finished first. The harness (and now
  full-population evaluation) is ready for it whenever that happens.

### Roadmap

- A real scoring-weight comparison run, once prioritized.
- Test coverage for `ground_truth.py`/`run_evaluation.py`.

## Resource Footprint and Scaling

**Ollama is the real concurrency bottleneck in this platform today — measured, not assumed.** A
real concurrent load test against the one endpoint that calls it synchronously
(`POST /extract` — see `docs/architecture/application-services/skill-extraction.md` for the full
numbers) found throughput dropping from 0.28 to 0.16 req/s and p95 latency rising from ~18s to
~58s as concurrency went from 3 to 6 — the same queuing signature the matching API showed before
its multi-worker fix (`docs/operations/production-readiness.md`), except here each unit of work is
seconds of real CPU-bound inference, not a database query, and there is no equivalent fix already
applied. `ollama` runs as a single instance with no `OLLAMA_NUM_PARALLEL` set (confirmed absent
from `docker-compose.yml`) and no GPU attached (CPU-only inference on this dev machine — the
RTX 5060 Laptop GPU measured in `docs/operations/infrastructure-sizing.md` sits unused). The real
scaling path — not built here, consistent with this project's no-real-cloud-provisioning
discipline (assumption A1) — would be a GPU node plus either multiple Ollama replicas behind a
small round-robin or a request queue in front of a single instance; `OLLAMA_NUM_PARALLEL` tuned
against measured VRAM would be the next cheap experiment before reaching for either. Full worked
scenario and raw numbers: `docs/operations/capacity-planning.md`.

## Related Documents

- [application-services/matching-engine.md](application-services/matching-engine.md)
  — the scoring logic this evaluates, and the N+1 fix that unlocked full-population
  evaluation
- [data-platform/embedding-matching.md](data-platform/embedding-matching.md) — the
  three-way rule-based/blended/embeddings-only comparison built on top of this same
  metrics harness, and the second real consumer of model serving
- [application-services/skill-extraction.md](application-services/skill-extraction.md)
  — Use Case C, the first real consumer of model serving
- [data-platform/data-at-scale.md](data-platform/data-at-scale.md) — the
  domain-anchored data change behind evaluation Round 3
- [operations/infrastructure-sizing.md](../operations/infrastructure-sizing.md) —
  current full-stack resource measurement
- [assumptions-register.md](../assumptions-register.md) — assumption A5/A5b, which
  the evaluation work resolves
- [architecture.md](../architecture.md) — overall system architecture
- `docs/progress.md` — full build history, kept locally as an internal working log, not included in this repository
