# Production Readiness Gap Assessment (Phase 13, updated through Phase 18)

Status: v0.3 — honest assessment, written for this exact purpose: per `docs/prd.md`, "this is not a
claim of production readiness... an honest gap assessment before any such claim is made." This
document is that gap assessment, not a victory lap. Every item below is either verified (with how),
or marked as a gap (with what closing it would take). Originally written at Phase 13; the
Functional DoD and "still required" list below were updated once Phases 14–18 (behavioral
analytics, model serving, skill extraction, embedding matching, and the Phase 18 gap-closing pass
below) closed some of what it originally flagged as missing — nothing else in this document was
rewritten to look better in hindsight.

## Definition of Done (from `docs/prd.md` §9) — scored honestly

### Functional
- [x] **Synthetic data generates reproducibly from a fixed seed** — verified repeatedly (Phase 2
  MD5-hash check, Phase 8 re-verified after a lint-driven refactor touched the generator).
- [x] **Use cases A–F work end-to-end against synthetic data** — A (candidate→job ranking), B
  (job→candidate ranking), D (skill-gap analysis), E (course recommendations) built since Phase 6.
  **C (free-text skill extraction, Phase 16) and F (embedding-based semantic matching, Phase 17)**
  were built later as post-v1 additions — see `docs/skill-extraction.md` and
  `docs/embedding-matching.md`. Both have a measured, honest baseline (extraction F1 0.504, not
  yet tuned; embeddings-blended ranking modestly beats rule-based-only on NDCG/MRR) rather than a
  claim of being "solved" — see those docs' own known-limitations sections.
- [x] **Ranking quality is evaluated (Precision@K, NDCG@K, MRR), not just "it runs"** — Phase 4/9:
  full-population evaluation (1,899 candidates), results in `docs/evaluation-strategy.md`.

### Engineering
- [x] **Automated tests cover matching logic and API endpoints** — Pure matching logic (scoring,
  eligibility, skill-gap, course recommendations) has 53+ unit tests. **Phase 18 closed the real
  gap**: `rank.py`'s orchestration and route-level API business behavior (not just auth
  boundaries) now have automated tests too (`tests/matching/test_rank_db.py`,
  `tests/api/test_routes_db.py`), run against a real Postgres transaction that's rolled back after
  each test (verified safe before trusting it, not assumed — see those files' docstrings), with CI
  now running a live Postgres service container so these aren't locally-only.
- [x] **Application runs fully containerized from a clean checkout** — Phase 7, re-verified via
  fresh-volume teardown/rebuild; Phase 10 additionally verified it runs with zero network access.
- [x] **CI pipeline runs lint + tests + build on every change** — Phase 8, verified green
  end-to-end including a fresh-volume smoke test (catches exactly the kind of "works on my
  machine" gap a weaker CI setup would miss — and did, twice, during Phase 8 itself).

### Operational
- [~] **CPU/RAM/latency/error-rate are observable** — Latency and error-rate: yes, fully verified
  (Phase 9, Prometheus + Grafana, multiprocess-aggregated metrics as of this phase's bottleneck
  fix — see below). CPU/RAM: wired (cAdvisor in Compose) but **doesn't render data on this Docker
  Desktop (Windows) dev machine** — a documented environment limitation (`docs/observability.md`),
  expected to work on a native Linux host. In Kubernetes, no CPU/RAM metrics path exists at all yet
  (kubelet's built-in cAdvisor endpoint needs RBAC wiring not yet done — `docs/kubernetes.md`).
- [x] **At least one load test has been run and bottlenecks identified** — see below; this was
  the one DoD item with zero prior work, closed in this phase.

## Load test: a real bottleneck found and fixed in this phase

`scripts/load_test.py` (concurrent `httpx` requests, client-side latency timing) against the live
API, 3 runs at increasing concurrency:

| Concurrency | Requests | Throughput | p50 | p95 | p99 |
|---|---|---|---|---|---|
| 5 | 100 | 16.1 req/s | 0.30s | 0.39s | 0.46s |
| 20 | 500 | 14.2 req/s | 1.33s | 1.84s | 3.41s |
| 50 | 300 | 14.7 req/s | 3.38s | 3.76s | 3.93s |

**The signature**: throughput plateaus at ~15 req/s regardless of concurrency, while latency
scales roughly linearly with load — work queuing up behind a single constrained resource, not the
system handling more concurrent work. Root cause, confirmed (not guessed): `docker/Dockerfile.api`
ran a single `uvicorn` worker process; scoring is CPU-bound synchronous Python (ranking up to 1,000
jobs per request), serialized by the GIL within that one process.

**Fix, verified**: added `--workers 4` to the uvicorn command. Re-ran the concurrency=20 case:
throughput **14.2 → 56.1 req/s** (~4x), p50 **1.33s → 0.34s**, p95 **1.84s → 0.57s**.

**A second real bug found while verifying the fix**: 4 worker processes each keep their own
in-memory Prometheus counters — `/metrics` under repeated scraping showed counts *alternating*
(122, 124, 122, 124...) rather than accumulating, because each scrape just hit whichever worker
happened to answer. Fixed with `prometheus_client`'s documented multiprocess mode
(`PROMETHEUS_MULTIPROC_DIR`, a custom `/metrics` route using `MultiProcessCollector` — see
`app/api/main.py`). Verified by sending requests in two batches and confirming the count
accumulated correctly (3 → 8) across repeated scrapes, not reset or duplicated.

This is the clearest example in this whole project of why "verify, don't assume" matters: the
multi-worker fix would have shipped with silently broken observability if the metrics hadn't been
checked after making the change that was supposed to only affect throughput.

**What this load test does NOT cover** (explicitly, not silently): no multi-endpoint mix (only
`job-matches` was tested), no test against the Kubernetes deployment under load (only Compose), no
chaos/failure-injection testing (pod kills, network partitions). Assumption A6 (latency
placeholders) is now partially resolved — concurrent-load numbers exist — but a production
capacity plan would need more scenarios than this one.

## Soak test (Phase 18): no degradation found over a sustained run

`scripts/load_test.py --soak-minutes` runs continuously at a fixed concurrency and reports one
summary per time window, rather than one aggregate number — the only way to see a trend (a slow
leak, growing latency, connection pool exhaustion) rather than a single point-in-time snapshot.

A real 3-minute run at concurrency=10, 30-second windows, against the `--workers 4` fix above:

| Window | n | Throughput | p50 | p95 | p99 |
|---|---|---|---|---|---|
| 1 | 860 | 28.6 req/s | 0.187s | 0.761s | 4.351s |
| 2 | 1139 | 37.9 req/s | 0.172s | 0.636s | 0.765s |
| 3 | 1077 | 35.9 req/s | 0.180s | 0.727s | 0.868s |
| 4 | 1138 | 37.9 req/s | 0.169s | 0.637s | 0.796s |
| 5 | 1169 | 38.8 req/s | 0.169s | 0.614s | 0.710s |
| 6 | 1114 | 37.1 req/s | 0.175s | 0.638s | 0.845s |

**Reading this honestly**: windows 2–6 are stable (p50 ~0.17–0.18s, throughput ~36–39 req/s, no
drift in either direction) — no evidence of a memory leak or connection-pool exhaustion over this
run's duration. Window 1's p99 (4.35s) is a cold-start outlier (connection-pool/JIT warmup), not a
sign of degradation — it disappears immediately in window 2 and never recurs. All 6,497 requests
across the run returned 200.

**Still not covered**: this is 3 minutes, not the "hours" a real soak test implies — long enough to
rule out an immediate/fast leak, not long enough to rule out a slow one. A real capacity-planning
soak test would run this for hours against a realistic endpoint mix, not one endpoint for minutes.

## Security

See `docs/security-and-threat-model.md` in full. Summary: static API key (not OAuth2/JWT — an
explicit, documented MVP choice for this single-tenant project, not an oversight), no rate
limiting, no TLS termination at the API itself, image/dependency scanning wired (Phase 8, Trivy +
Dependabot) but report-only (no accepted-baseline triage has happened, so nothing fails the build
on a CRITICAL finding yet).

## Data

Synthetic, reproducible, documented imperfections (`docs/data-model.md`). No real PII ever
involved. No backup/restore procedure has been built or tested for the Postgres volume — a real
gap for anything beyond a disposable dev/demo environment.

## Scope and confidentiality

No HRDF confidential material was used in design, data, or code at any point — maintained by
discipline (never opening files outside this project's own directory), not by a technical control.
This can't be "verified" the way a test can; it's an attestation, recorded here because the
Definition of Done asks for an honest accounting and this is a load-bearing constraint of the
entire project, not an incidental one.

## What "production ready" would still require, beyond this project's scope

- A real secrets manager (not `.env`/K8s Secret placeholders).
- TLS and a real authN/authZ model (OAuth2/JWT + roles) if ever multi-tenant. Basic rate limiting
  was added in Phase 18, but imprecisely under `api`'s multiple worker processes — a true global
  limit needs a shared counter store (Redis), the first concrete, *measured* case in this project
  where that tool would help, not a hypothetical one — see `docs/security-and-threat-model.md`.
- Making the synchronous Ollama-calling paths (Phase 14's analytics instrumentation hook, Phase
  16's extraction endpoint) non-blocking under real concurrent load — a message queue (also
  plausibly Redis-backed) is the documented future answer, triggered by a measured throughput
  problem, not built speculatively now (see each phase's own "v4" roadmap entries).
- kubelet-cadvisor RBAC scraping (or an equivalent) for container CPU/RAM in Kubernetes.
- A real, hours-long soak test and a real capacity plan — Phase 18's 3-minute soak (above) ruled
  out an immediate leak/degradation, not a slow one, and only exercised one endpoint.
- The Azure overlay actually deployed and verified against real AKS (per assumption A1 — not done
  by design, zero cloud cost, but that means zero cloud-specific verification too).
- Use Case C/F tuning and promotion decisions (model/prompt comparison for extraction; whether
  blended embeddings ranking is worth promoting over rule-based) — both have a measured baseline,
  neither has been iterated on yet.

None of this is hidden or assumed away — that's the point of this document existing.
