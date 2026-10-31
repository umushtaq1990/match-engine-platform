# Redis — Rate Limiting, Result Caching, Async Streaming

Status: v2 — **implemented and verified against real running containers**, all three v1 use cases
plus v2 hardening (active cache invalidation, consumer retry/dead-letter handling). Same discipline
as everywhere else in this project: design written and reviewed first, then built, then measured —
not assumed from the design.

## Purpose & Scope

Redis was deliberately not added earlier in this project — it was flagged as a *future* fix for a
documented limitation, not a present need, until the need stopped being hypothetical: a real
measured bug (rate-limit counters inflated under `api`'s 4 worker processes) and a real scale target
(recomputing a full ranking per request at 1M-candidate/50K-job scale is wasteful, and synchronous
instrumentation calls become a real bottleneck under real concurrency) made it necessary. Three
concrete, independently-scoped use cases — not "add Redis" as a vague capability:

1. **Rate-limiting backend** — fixes a measured multi-worker counter-inflation bug.
2. **Result caching** — 1-hour TTL cache-aside on the matching endpoints, with active invalidation
   layered on top.
3. **Redis Streams** — async decoupling of the analytics instrumentation hook, with retry/
   dead-letter handling for a crashed consumer.

## Architecture & Design

### Tool: official `redis:7.4.12-alpine`, one shared instance

Confirmed via Docker Hub's API, same pattern as every other image in this project. One instance
serves all three use cases below — not three separate Redis deployments. A production system at
real scale might split cache/rate-limit/stream-broker onto separate instances or a cluster;
documented as a future step, not built now, since nothing here has outgrown a single instance yet.

### Use case 1: rate-limiting backend — fixes a measured bug directly

`slowapi`'s default in-memory storage gives each of `api`'s 4 uvicorn worker processes an
independent counter — a real test let 122 requests through before 429s, not a clean 100. The
`limits` library (already a transitive dependency via `slowapi`) supports a Redis-backed
`storage_uri` natively — swapping `Limiter(key_func=..., storage_uri="redis://redis:6379")` is the
entire code change per service.

### Use case 2: result caching, 1-hour TTL

Cache-aside on the matching endpoints (`/candidates/{id}/job-matches`, the `/semantic` variant):
check Redis first on a cache key derived from `(candidate_id, top_k, include_ineligible, mode)`;
on a miss, compute as usual and write the result with `EX 3600` (1 hour); on a hit, skip
computation entirely. An `X-Cache: HIT`/`MISS` response header makes the behavior directly
demonstrable, not just an invisible internal optimization.

**Why 1 hour specifically**: matches an "active user session" framing — long enough that a user
paging through results or reloading doesn't recompute a full ranking against 50,000 jobs each
time, short enough that a profile/job-posting change becomes visible within a bounded, documented
window rather than never.

**Active cache invalidation, scoped to the one real mutation path that exists**: there is still no
candidate/job *profile* mutation endpoint anywhere in this API (extraction's `/extract` is
read-only; data only changes via the seed generator or `embeddings-compute`) — so "invalidate on
profile change" can't be built against a trigger that doesn't exist, honestly, not papered over.
What *does* change a candidate's job-matches ranking today is `embeddings-compute` recomputing
their embeddings (the default ranking mode depends on them — see `docs/embedding-matching.md`).
`app/embeddings/cache_invalidation.py` implements a **version-counter** scheme rather than a
`SCAN`/pattern-delete sweep: each candidate's cache key is stamped with a version (`GET
jme:cache:version:candidate:{id}`, default 0); invalidation is a single `INCR` — O(1), no blocking
scan over the keyspace — and the old versioned entries simply age out via their own TTL instead of
needing to be found and deleted.

### Use case 3: Redis Streams for async decoupling — the more architecturally interesting one

Not Celery, not RQ — **Redis Streams** directly (`XADD`/`XREADGROUP`/`XACK`), because it
demonstrates real event-streaming architecture (producer/consumer groups, at-least-once delivery)
rather than wrapping a generic task-queue library, and it maps directly onto something that
already exists: the analytics instrumentation hook.

**Before**: the matching API made a synchronous, best-effort HTTP `POST` to the analytics service
on every request — if analytics was unreachable, the call failed fast and the event was silently
dropped (by design, documented in `app/api/analytics_client.py`).

**With Streams**: the matching API calls `XADD` instead — near-instant, and durable (the stream
persists events up to a configured `MAXLEN` even if the analytics service is down entirely,
strictly better than the old "down means dropped" behavior). The analytics service runs a
background consumer (a consumer group, so multiple analytics replicas could one day split the
work) that reads events via `XREADGROUP`, writes them to Postgres, and `XACK`s only after a
successful write. The existing `POST /events`/`/events/batch` HTTP endpoints stay as-is for
direct/manual event submission — both paths coexist, demonstrating that async internal
instrumentation and a synchronous public API aren't mutually exclusive designs.

**Consumer-group retry/dead-letter handling**: `app/analytics/stream_consumer.py` calls
`XPENDING`/`XCLAIM` each loop iteration to find messages idle longer than 30s (claimed by some
consumer — possibly one that crashed mid-processing — and never acked). A message under 3 delivery
attempts is reclaimed and retried; at or beyond 3, it's moved to a `:dead-letter` stream instead of
blocking the main stream forever.

### Service boundary

Redis is a dependency of `api` (rate limiting, caching, stream producer) and `analytics` (rate
limiting, stream consumer) — no new deployable image, just a new official-image service in
`docker-compose.yml` and a `redis` Python client added to both services' requirements files.
`app/embeddings/cache_invalidation.py` (the version-counter helper) lives in `app/embeddings/`
rather than `app/api/` specifically so it can be imported from both the `api` image and the
`extraction`-based `embeddings-compute` image without crossing that service boundary.

## Implementation

- `app/api/rate_limit.py`, `app/analytics/rate_limit.py` — `storage_uri=REDIS_URL` passed to
  `slowapi`'s `Limiter`.
- `app/api/cache.py` — cache-aside read/write helpers; `candidate_cache_key()` incorporates the
  version-counter from `app/embeddings/cache_invalidation.py`.
- `app/embeddings/cache_invalidation.py` — the version-counter invalidation scheme, called from
  `app/embeddings/compute_embeddings.py` whenever a candidate's embeddings actually change.
- `app/api/stream_client.py` (producer, `XADD`) and `app/analytics/stream_consumer.py` (consumer,
  `XREADGROUP`/`XACK`, plus `XPENDING`/`XCLAIM` retry and dead-letter handling).

## Verification & Results

Built and verified against the real stack (`db`, `redis`, `api`, `analytics`, all actual
containers — nothing mocked), using the same before/after discipline used throughout this project.

**Use case 1 — rate limiting.** Re-ran the exact prior test (150 rapid concurrent requests, one
API key, `ThreadPoolExecutor(max_workers=20)` against `/candidates/1/job-matches`) against the
Redis-backed limiter (`app/api/rate_limit.py`'s `storage_uri=REDIS_URL`): **100 requests succeeded,
50 got 429s** — a clean, exact split across `api`'s 4 uvicorn worker processes, replacing the
previously-measured ~122-through-before-429s inflation. Confirmed this isn't a per-service bucket
collision risk despite `api` and `analytics` sharing one Redis instance: `slowapi` scopes its Redis
key by `(key_func_result, endpoint_identity)`, and the two services' endpoint functions are never
named the same — verified directly by instantiating `limits.storage.RedisStorage` and inspecting
the actual Redis keys it writes (`LIMITS:LIMITER/<key>/<endpoint>/100/1/minute`), not assumed.

**Use case 2 — result caching.** Called `GET /candidates/5/job-matches` twice: first response
carried `X-Cache: MISS`, second carried `X-Cache: HIT`, and `redis-cli TTL` on the stored key
confirmed it was set with ~3590 seconds remaining (a `1-hour` TTL, timing-consistent with the few
seconds the test took to run). A cached hit still re-logs a `recommendation_shown` impression
(re-showing a recommendation is still an impression, whether recomputed or not).

**Active cache invalidation.** Computed embeddings for a candidate with none before, confirmed the
version key went `None -> 1`, and the identical request that previously returned a cached
`rule_based_no_embeddings` MISS-then-HIT pair came back as a **fresh MISS, `blended`** — the stale
fallback result never stayed queryable past the moment it became wrong, not just "after up to an
hour."

**Use case 3 — Redis Streams.** After a batch of matching requests, `XINFO GROUPS
analytics:recommendation_events` showed `consumers: 1`, `pending: 0`, and `entries-read` matching
`XLEN` exactly — the background consumer thread is keeping up with zero backlog. Confirmed the
events actually land in Postgres via the stream path, not just get acknowledged: `select count(*)
from analytics.behavioral_events where source='api'` increased after each matching request, with
the correct `actor_id`/`entity_id`/`metadata` content, exactly as the old synchronous HTTP path
would have produced — proving the two paths are behaviorally equivalent from the caller's
perspective, just decoupled now.

**Consumer retry/dead-letter handling.** Deliberately simulated a crash: `XADD`'d a message, read
it with a throwaway consumer that never acked it (standing in for a process that died
mid-handling), then confirmed the real background consumer reclaimed it ~30s later via `XCLAIM`,
processed it, and the row landed correctly in Postgres — `XPENDING` showed zero entries afterward,
dead-letter stream untouched (correctly, since it succeeded within the retry budget).

**Fail-open design, verified by the existing test suite, not just argued.** `REDIS_URL` is unset in
the bare `pytest` run (no Redis container) — the full test suite passes unchanged, confirming
`app/api/cache.py`, `app/api/stream_client.py`, and `app/embeddings/cache_invalidation.py` degrade
to a clean miss/HTTP-fallback/no-op rather than raising when Redis isn't configured — the same
best-effort doctrine `app/api/analytics_client.py` already used before Redis existed in this
project.

## Known Limitations

- Single Redis instance, no Sentinel/Cluster HA — acceptable for a single-developer local
  platform, a real gap for anything beyond it.
- Cache invalidation only covers the candidate-embeddings trigger above — there's still no
  candidate/job profile mutation endpoint to invalidate against; TTL remains the only staleness
  bound for any other kind of change. A job's own embedding changing doesn't invalidate any
  candidate's cache either — no traceable "which candidates cached this job" index exists, a real,
  documented gap, not an oversight.
- Dead-letter messages are appended to a stream, not alerted on or automatically replayed — a human
  (or a future tool) has to go look.

## Roadmap

- Splitting cache/broker onto separate Redis instances, only if one instance's load ever actually
  becomes a bottleneck.
- Alerting/automated replay for the dead-letter stream.
- Invalidating a job's cache impact on candidates once a traceable index exists.

## Related Documents

- [`docs/embedding-matching.md`](embedding-matching.md) — the blended ranking path whose cache this
  subsystem invalidates on embeddings recompute.
- [`docs/behavioral-analytics.md`](behavioral-analytics.md) — the instrumentation hook this
  subsystem's Streams use case decouples.
- [`docs/security-and-threat-model.md`](security-and-threat-model.md) — the rate-limiting fix in
  context of the broader threat model.
- [`docs/architecture.md`](architecture.md) — system-wide overview.
- [`docs/progress.md`](progress.md) — full build history.
