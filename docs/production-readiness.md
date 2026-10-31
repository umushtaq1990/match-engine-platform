# Production Readiness Gap Assessment

Status: honest assessment, written for this exact purpose: per `docs/prd.md`, "this is not a claim
of production readiness... an honest gap assessment before any such claim is made." This document
is that gap assessment, not a victory lap. Every item below is either verified (with how), or
marked as a gap (with what closing it would take). Originally written early in the project; updated
repeatedly as later work closed some of what it originally flagged as missing — nothing in this
document is rewritten to look better in hindsight, only to stay current.

## Purpose & Scope

Scores this project honestly against its own Definition of Done, documents every load/soak test
run and what was found, and lists exactly what "production ready" would still require beyond this
project's scope. In scope: engineering/operational readiness. Out of scope: a real production
deployment (never done, by design — see `docs/assumptions-register.md` A1).

## Architecture & Design

The assessment is organized around `docs/prd.md` §9's Definition of Done, scored per item with
either verification evidence or an explicit gap. Load/soak testing uses `scripts/load_test.py`
(concurrent `httpx` requests, client-side latency timing) against the live API — chosen as a
lightweight, dependency-free way to get real concurrency numbers without standing up a separate
load-testing tool for a single-developer project.

## Implementation

`scripts/load_test.py` supports both a fixed-concurrency burst mode and a `--soak-minutes` mode
that reports one summary per time window rather than one aggregate number — the only way to see a
trend (a slow leak, growing latency, connection pool exhaustion) rather than a single point-in-time
snapshot.

## Verification & Results

### Definition of Done — scored honestly

**Functional**
- [x] **Synthetic data generates reproducibly from a fixed seed** — verified repeatedly (an MD5-hash
  check originally, re-verified after a lint-driven refactor touched the generator, and again after
  the Phase 20 domain-anchored-sampling rewrite).
- [x] **Use cases A–F work end-to-end against synthetic data** — A (candidate→job ranking), B
  (job→candidate ranking), D (skill-gap analysis), E (course recommendations) built early. **C
  (free-text skill extraction) and F (embedding-based semantic matching)** were built later — see
  `docs/skill-extraction.md` and `docs/embedding-matching.md`. Both have a measured baseline
  (extraction F1 0.664 after tuning; embeddings-blended ranking beats rule-based-only on NDCG/MRR
  and was promoted to the default ranking path) rather than a claim of being "solved from day one"
  — see those docs' own Known Limitations.
- [x] **Ranking quality is evaluated (Precision@K, NDCG@K, MRR), not just "it runs"** — full-
  population evaluation, results in `docs/evaluation-strategy.md`.

**Engineering**
- [x] **Automated tests cover matching logic and API endpoints** — pure matching logic (scoring,
  eligibility, skill-gap, course recommendations) has extensive unit tests, and `rank.py`'s
  orchestration plus route-level API business behavior (not just auth boundaries) have automated
  tests too (`tests/matching/test_rank_db.py`, `tests/api/test_routes_db.py`), run against a real
  Postgres transaction that's rolled back after each test (verified safe before trusting it, not
  assumed — see those files' docstrings), with CI running a live Postgres service container so
  these aren't locally-only.
- [x] **Application runs fully containerized from a clean checkout** — verified via fresh-volume
  teardown/rebuild, and separately verified to run with zero network access
  (`docs/offline-deployment.md`).
- [x] **CI pipeline runs lint + tests + build on every change** — verified green end-to-end
  including a fresh-volume smoke test (catches exactly the kind of "works on my machine" gap a
  weaker CI setup would miss — and did, more than once, during early CI setup).

**Operational**
- [~] **CPU/RAM/latency/error-rate are observable** — latency and error-rate: yes, fully verified
  (Prometheus + Grafana, multiprocess-aggregated metrics — see Load test below). CPU/RAM: wired
  (cAdvisor in Compose) but **doesn't render data on this Docker Desktop (Windows) dev machine** —
  a documented environment limitation (`docs/observability.md`), expected to work on a native Linux
  host. In Kubernetes, no CPU/RAM metrics path exists yet (kubelet's built-in cAdvisor endpoint
  needs RBAC wiring not yet done — `docs/kubernetes.md`).
- [x] **At least one load test has been run and bottlenecks identified** — see below.

### Load test: a real bottleneck found and fixed

Concurrent `httpx` requests against the live API, 3 runs at increasing concurrency:

| Concurrency | Requests | Throughput | p50 | p95 | p99 |
|---|---|---|---|---|---|
| 5 | 100 | 16.1 req/s | 0.30s | 0.39s | 0.46s |
| 20 | 500 | 14.2 req/s | 1.33s | 1.84s | 3.41s |
| 50 | 300 | 14.7 req/s | 3.38s | 3.76s | 3.93s |

**The signature**: throughput plateaus at ~15 req/s regardless of concurrency, while latency scales
roughly linearly with load — work queuing up behind a single constrained resource, not the system
handling more concurrent work. Root cause, confirmed (not guessed): `docker/Dockerfile.api` ran a
single `uvicorn` worker process; scoring is CPU-bound synchronous Python (ranking up to 1,000 jobs
per request), serialized by the GIL within that one process.

**Fix, verified**: added `--workers 4` to the uvicorn command. Re-ran the concurrency=20 case:
throughput **14.2 → 56.1 req/s** (~4x), p50 **1.33s → 0.34s**, p95 **1.84s → 0.57s**.

**A second real bug found while verifying the fix**: 4 worker processes each kept their own
in-memory Prometheus counters — `/metrics` under repeated scraping showed counts *alternating*
(122, 124, 122, 124...) rather than accumulating, because each scrape just hit whichever worker
happened to answer. Fixed with `prometheus_client`'s documented multiprocess mode
(`PROMETHEUS_MULTIPROC_DIR`, a custom `/metrics` route using `MultiProcessCollector` — see
`app/api/main.py`). Verified by sending requests in two batches and confirming the count
accumulated correctly (3 → 8) across repeated scrapes, not reset or duplicated. This is the
clearest example in this whole project of why "verify, don't assume" matters: the multi-worker fix
would have shipped with silently broken observability if the metrics hadn't been checked after
making a change that was supposed to only affect throughput.

**What this load test does NOT cover** (explicitly, not silently): no multi-endpoint mix (only
`job-matches` was tested), no test against the Kubernetes deployment under load (only Compose), no
chaos/failure-injection testing (pod kills, network partitions).

### Soak test: no degradation found over a sustained run

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

### Security, data, and scope — summarized

- **Security**: static API key (not OAuth2/JWT — an explicit, documented MVP choice for this
  single-tenant project, not an oversight), Redis-backed rate limiting (fixed a real measured
  multi-worker counter-inflation bug — see `docs/redis-infrastructure.md`), no TLS termination at
  the API itself, image/dependency scanning wired (Trivy + Dependabot) but report-only (no
  accepted-baseline triage has happened, so nothing fails the build on a CRITICAL finding yet). Full
  detail: `docs/security-and-threat-model.md`.
- **Data**: synthetic, reproducible, documented imperfections (`docs/data-model.md`). No real PII
  ever involved. A `pg_dump`/`pg_restore` backup/restore procedure exists and has been verified
  end-to-end (`docs/backup-and-recovery.md`) — not automated/scheduled, a real gap for anything
  beyond a disposable dev/demo environment.
- **Scope and confidentiality**: no confidential prior-employer material was used in design, data,
  or code at any point — maintained by discipline (never opening files outside this project's own
  directory), not by a technical control. This can't be "verified" the way a test can; it's an
  attestation, recorded here because the Definition of Done asks for an honest accounting and this
  is a load-bearing constraint of the entire project, not an incidental one.

## Known Limitations

What "production ready" would still require, beyond this project's scope:

- A real secrets manager (not `.env`/K8s Secret placeholders).
- TLS and a real authN/authZ model (OAuth2/JWT + roles) if ever multi-tenant.
- Making the extraction endpoint's own Ollama-calling path non-blocking under real concurrent load
  — the analytics instrumentation hook was already decoupled via Redis Streams
  (`docs/redis-infrastructure.md`), extraction's own path has not been.
- kubelet-cadvisor RBAC scraping (or an equivalent) for container CPU/RAM in Kubernetes.
- A real, hours-long soak test and a real capacity plan — the 3-minute soak above ruled out an
  immediate leak/degradation, not a slow one, and only exercised one endpoint.
- The Azure overlay actually deployed and verified against real AKS (per assumption A1 — not done
  by design, zero cloud cost, but that means zero cloud-specific verification too).
- Single Redis instance, no HA; cache invalidation only covers the one real mutation trigger that
  exists today (embeddings recompute) — there is still no candidate/job profile mutation API to
  invalidate a wider set of changes against (`docs/redis-infrastructure.md`).

None of this is hidden or assumed away — that's the point of this document existing.

## Related Documents

- `docs/architecture.md` — overview and quality requirements summary
- `docs/progress.md` — full build history
- `docs/prd.md` — the Definition of Done this assessment scores against
- `docs/security-and-threat-model.md`, `docs/redis-infrastructure.md` — security/rate-limiting detail
- `docs/observability.md` — the Prometheus/Grafana/cAdvisor stack referenced above
- `docs/backup-and-recovery.md` — the backup/restore procedure referenced above
- `docs/kubernetes.md` — the Kubernetes-specific gaps referenced above
- `docs/infrastructure-sizing.md` — resource sizing this assessment's load test informed
- `docs/assumptions-register.md` — assumption A1 (no real Azure subscription), A6 (latency figures)
