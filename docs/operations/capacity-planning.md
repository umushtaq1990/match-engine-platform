# Capacity Planning: Concurrent-User Scaling Story

Status: v1 — the API-layer concurrency numbers are real and already published
(`docs/operations/production-readiness.md`); the LLM/embedding-path concurrency numbers below are a
real, live measurement run specifically for this doc, not a single-request projection.

## Purpose & Scope

Every other resource-sizing doc in this project (`docs/operations/infrastructure-sizing.md`)
measures static footprint: per-service memory/CPU at rest or under one workload, Kubernetes
requests/limits, an illustrative cost. None of that answers the question an AI Architect actually
needs to answer for a real deployment: **if N users were hitting this platform concurrently, where
does it break first, and what's the real fix?** This doc is that answer, built from measurements,
not guesses, and it is deliberately the one place this question gets answered in full — every
architecture-layer doc links here instead of repeating the numbers.

In scope: the concurrency ceiling of every layer in the real request path (ingress through to
Postgres/Redis/Ollama), and the honest next step for each. Out of scope: actually provisioning any
of those next steps (same no-real-cloud discipline as everywhere else in this project — assumption
A1).

## Architecture & Design

The methodology mirrors `scripts/load_test.py`'s existing, already-proven approach (Phase 13): real
concurrent `httpx` requests against the live, running stack, client-side latency timing, increasing
concurrency until a bottleneck shows itself as throughput plateauing while latency climbs — not
synthetic estimation. This doc adds exactly one new measurement that didn't exist before: concurrent
load against the one live endpoint that calls Ollama synchronously (`POST /extract`,
`scripts/load_test_llm.py`), since every endpoint previously load-tested (`job-matches`) never
touches the LLM/embedding serving layer at all.

**A real finding worth stating plainly before any numbers**: there is no live HTTP endpoint for
embeddings at all. `app/embeddings/` is batch-job-only (`embeddings-compute`, run via
`docker compose run --rm embeddings-compute`) — candidates/jobs get their embeddings computed
offline, not on a live request path. So "concurrent users calling the embeddings API" isn't a real
scenario this platform exposes today; the only live LLM/embedding-adjacent path a concurrent user
can actually hit is skill extraction (`POST /extract`), which itself calls both the LLM (one
`generate()` call) and the embedding model (one `embed()` call per extracted skill phrase,
sequentially) — see `docs/architecture/application-services/skill-extraction.md`.

## Implementation

`scripts/load_test_llm.py` — same shape as the existing `scripts/load_test.py` (plain `httpx` +
`ThreadPoolExecutor`, no new dependency), run against `POST /extract` at concurrency 1, 3, and 6
with a rotating set of representative job/CV description texts, both through the real Gateway path
(`http://localhost:8099/extraction/extract`, the same path the Frontend itself uses) and directly
against `extraction:8002` to isolate whether nginx adds any meaningful overhead of its own.

Running this required the `qwen2.5:1.5b`/`nomic-embed-text`/`all-minilm` models to actually be
pulled into the `ollama` service's named volume first (`docker compose run --rm ollama-pull`) — a
real ~1.2GB download. `scripts/backup_ollama_models.sh`/`restore_ollama_models.sh` (new, mirroring
`backup_db.sh`/`restore_db.sh`'s existing pattern) now tar that volume to `backups/` after a pull,
specifically so this re-download never has to happen again on this machine even if the volume is
ever wiped or recreated.

## A worked scenario: N concurrent users through the full request chain

Walking the real path (`Load Balancer -> Reverse Proxy -> Gateway -> {api, extraction} ->
{Postgres, Redis, Ollama}`) with the numbers that exist today:

| Hop | Concurrency model today | Real measured/known ceiling | Real next step if load grew |
|---|---|---|---|
| Load Balancer / Reverse Proxy / Gateway (nginx) | Stateless, 2 Reverse Proxy replicas | Not the bottleneck at any scale this project has tested — confirmed via `docker stats` idling at single-digit MB | Add replicas; nothing architecturally new |
| `api` (matching) | 4 uvicorn workers | 56.1 req/s at concurrency 20 (was 14.2 req/s at 1 worker) — `docs/operations/production-readiness.md` | Already has a k8s HPA (2-5 pods, `k8s/base/api.yaml`) |
| `extraction` (skill extraction / LLM path) | 1 uvicorn worker, synchronous route | See below — real measured numbers | Add `--workers N` (same fix `api` got) plus the Ollama fix below |
| `ollama` (LLM + embeddings) | 1 instance, no `OLLAMA_NUM_PARALLEL` set (confirmed absent from `docker-compose.yml`) | See below | GPU node + either multiple Ollama replicas behind a small round-robin or a request queue — not built here |
| Postgres | 1 instance, `max_connections=100` (confirmed on the live container) | Up to ~90 possible connections from `api`/`analytics`/`extraction` alone at current worker counts (SQLAlchemy default `pool_size=5, max_overflow=10` per worker, `app/db.py`, no override configured) | PgBouncer, or raise `max_connections`, before adding a fourth heavy consumer |
| Redis | 1 instance, shared by rate limiting/cache/Streams | No contention observed at this project's scale | Split cache/broker onto separate instances only if ever actually needed (`docs/architecture/data-platform/redis-infrastructure.md` Roadmap) |

### The LLM/embedding path, measured under real concurrency

Real `scripts/load_test_llm.py` runs against the live stack (`POST /extract`, direct to
`extraction:8002` unless noted), concurrency 1/3/6:

| Concurrency | Requests | Throughput | p50 | p95 | max | mean matches/request |
|---|---|---|---|---|---|---|
| 1 | 4 | 0.04 req/s | 31.75s | 58.29s | 58.29s | 4.0 |
| 3 | 9 | 0.28 req/s | 10.94s | 17.96s | 17.96s | 1.7 |
| 6 | 12 | 0.16 req/s | 21.69s | 58.23s | 58.23s | 1.8 |
| 3 (through the real Gateway, not direct) | 6 | 0.18 req/s | 16.02s | 26.84s | 26.84s | 1.8 |

**A real, non-obvious finding that complicates a clean "throughput vs. concurrency" reading**: unlike
the matching API, `/extract`'s latency is not fixed per request — each call makes 1 LLM `generate()`
call plus 1 embedding call per extracted skill phrase, all sequential
(`app/extraction/pipeline.py`), and the LLM's own output length varies run to run even for similar
input text (it is not run at temperature 0). The "mean matches/request" column shows this directly:
the concurrency=1 run happened to extract more skills per call (mean 4.0) than the concurrency=3/6
runs (mean 1.7-1.8), which by itself would be expected to make concurrency=1 the *slowest*, not the
fastest. The true concurrency effect has to be read through that noise, not around it — which is why
the right comparison is concurrency=3 vs. concurrency=6 at similar mean skill counts (1.7 vs. 1.8):
**throughput dropped from 0.28 to 0.16 req/s and p95/max latency rose from ~18s to ~58s** as
concurrency doubled — the same queuing signature as the matching API's pre-fix bottleneck (Phase
13), just at a far smaller absolute scale because each unit of work here is seconds of real LLM
inference, not a database query. At concurrency 6, the tail latency (58.23s) sits right at the edge
of `ollama_client.py`'s 60s per-call timeout — a few requests away from real timeout failures, not a
comfortable margin.

The Gateway-routed run (concurrency=3, p50 16.02s) lands in the same range as the direct run at the
same concurrency (p50 10.94s) — the gap is consistent with the same LLM-output-length noise above,
not nginx overhead, which would be milliseconds against calls that take seconds. **nginx is not a
meaningful contributor to this layer's latency.**

**Confirmed, not assumed**: `ollama` has no `OLLAMA_NUM_PARALLEL` set, so this behavior is Ollama's
own CPU-only default — a real GPU-backed deployment, or an explicit `OLLAMA_NUM_PARALLEL` tuned
against measured VRAM, would be the first thing to test differently, not assumed to "just work
better."

## Verification & Results

Raw `scripts/load_test_llm.py` output, run against the live stack after `docker compose up -d
--build`, `migrate`, `seed`, and `ollama-pull`:

```
base_url=http://localhost:8002 concurrency=1 requests=4
n=4 throughput=0.04req/s statuses={200: 4} p50=31.75s p95=58.29s p99=58.29s mean=25.88s max=58.29s
skills_extracted: mean=4.0 min=3 max=5

base_url=http://localhost:8002 concurrency=3 requests=9
n=9 throughput=0.28req/s statuses={200: 9} p50=10.94s p95=17.96s p99=17.96s mean=10.58s max=17.96s
skills_extracted: mean=1.7 min=0 max=5

base_url=http://localhost:8002 concurrency=6 requests=12
n=12 throughput=0.16req/s statuses={200: 12} p50=21.69s p95=58.23s p99=58.23s mean=25.41s max=58.23s
skills_extracted: mean=1.8 min=0 max=5

base_url=http://localhost:8099/extraction concurrency=3 requests=6
n=6 throughput=0.18req/s statuses={200: 6} p50=16.02s p95=26.84s p99=26.84s mean=15.84s max=26.84s
skills_extracted: mean=1.8 min=0 max=5
```

All 31 requests across all four runs returned `200` — no timeouts or errors at these concurrency
levels, but concurrency=6's 58.23s max is close enough to the 60s per-call timeout that a modestly
higher concurrency level would likely start producing real failures, not just slow responses.

## Known Limitations

- Concurrency levels tested (1/3/6) are modest, matched to this project's single CPU-only Ollama
  instance on one dev machine — not a claim about what a GPU-backed, multi-replica deployment could
  sustain.
- No embeddings-specific concurrent load test exists, because no live embeddings endpoint exists to
  test (see above) — this is a documented gap in the platform's surface area, not an oversight in
  this doc.
- `analytics` was not separately load-tested — its Redis Streams decoupling means it's architecturally
  not on the critical path for matching-response latency regardless, but its own real ingestion
  ceiling under heavy event volume is unmeasured.
- This is still one dev machine, one point in time — the same caveat `docs/operations/production-readiness.md`
  already states for the `api` load test applies here too.

## Roadmap

- Re-run this measurement once/if `extraction` gets multiple workers, to confirm whether the real
  bottleneck shifts from the worker process to Ollama itself.
- A real queue (or Ollama's own concurrent-request handling, if a future version changes its
  defaults) in front of Ollama, if concurrent LLM load is ever a genuine production requirement
  rather than a documented architectural question.
- Extend `scripts/load_test_llm.py` into a soak-mode run (same `--soak-minutes` pattern as
  `scripts/load_test.py`) if a sustained-load LLM capacity question ever needs answering.

## Related Documents

- `docs/operations/production-readiness.md` — the `api` load test this doc builds on
- `docs/operations/infrastructure-sizing.md` — static per-service footprint and Kubernetes sizing
- `docs/architecture/application-services/skill-extraction.md` — the endpoint load-tested here
- `docs/architecture/ai-ml-and-mlops.md` — the Ollama serving layer this doc's real bottleneck sits in
- `docs/architecture/data-platform/README.md` — Postgres/Redis connection-pool detail
- `docs/assumptions-register.md` — assumption A6 (latency/concurrency measurement status)
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
