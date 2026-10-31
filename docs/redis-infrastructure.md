# Redis — Rate Limiting, Result Caching, Async Streaming (Phase 21, hardened Phase 25)

Status: v2 — **implemented and verified against real running containers**, all three v1 use cases
plus v2 hardening (active cache invalidation, consumer retry/dead-letter handling). Same discipline
as every prior phase: design written and reviewed first (below), then built, then measured — not
assumed from the design.

## Why now, not earlier

Redis has come up twice before in this project and was deliberately not added both times — Phase
13/18 flagged it as the *future* fix for a documented limitation, not a present need. It's added
now because the need stopped being hypothetical: Phase 18 **measured** the exact bug a shared store
fixes (rate-limit counters inflated under `api`'s 4 worker processes), and Phase 20's 1M-user/
50K-job scale target makes two more real: recomputing a full ranking per request at that volume is
wasteful, and synchronous instrumentation calls become a real bottleneck under real concurrency.
Three concrete, independently-scoped use cases — not "add Redis" as a vague capability.

## Tool: official `redis:7.4.12-alpine`, one shared instance for v1

Confirmed via Docker Hub's API, same pattern as every other image in this project. One instance
serves all three use cases below for v1 — not three separate Redis deployments. A production
system at real scale might split cache/rate-limit/stream-broker onto separate instances or a
cluster; documented as a future step, not built now, since nothing here has outgrown a single
instance yet.

## Use case 1: rate-limiting backend — fixes a measured bug directly

Phase 18 found `slowapi`'s default in-memory storage gives each of `api`'s 4 uvicorn worker
processes an independent counter — a real test let 122 requests through before 429s, not a clean
100. The `limits` library (already a transitive dependency via `slowapi`) supports a Redis-backed
`storage_uri` natively — swapping `Limiter(key_func=..., storage_uri="redis://redis:6379")` is the
entire code change per service. **To be verified directly, not assumed from documentation**: once
built, re-run Phase 18's exact test (150 rapid requests, one key) and confirm it now lets through
a clean ~100, not ~122 — the same before/after discipline every other phase has used.

## Use case 2: result caching, 1-hour TTL

Cache-aside on the matching endpoints (`/candidates/{id}/job-matches`, the `/semantic` variant):
check Redis first on a cache key derived from `(candidate_id, top_k, include_ineligible, mode)`;
on a miss, compute as today and write the result with `EX 3600` (1 hour); on a hit, skip
computation entirely. An `X-Cache: HIT`/`MISS` response header makes the behavior directly
demonstrable, not just an invisible internal optimization.

**Why 1 hour specifically**: matches "active user session" framing — long enough that a user
paging through results or reloading doesn't recompute a full ranking against 50,000 jobs each
time, short enough that a profile/job-posting change becomes visible within a bounded, documented
window rather than never. No active invalidation on data change in v1 — TTL expiry is the only
staleness bound, a deliberate, documented simplification (see limitations).

## Use case 3: Redis Streams for async decoupling — the more architecturally interesting one

Not Celery, not RQ — **Redis Streams** directly (`XADD`/`XREADGROUP`/`XACK`), because it
demonstrates real event-streaming architecture (producer/consumer groups, at-least-once delivery)
rather than wrapping a generic task-queue library, and it maps directly onto something that
already exists: Phase 14's analytics instrumentation hook.

**Today**: the matching API makes a synchronous, best-effort HTTP `POST` to the analytics service
on every request — if analytics is unreachable, the call fails fast and the event is silently
dropped (by design, documented in `app/api/analytics_client.py`).

**With Streams**: the matching API calls `XADD` instead — near-instant, and durable (the stream
persists events up to a configured `MAXLEN` even if the analytics service is down entirely,
strictly better than today's "down means dropped" behavior). The analytics service runs a
background consumer (a consumer group, so multiple analytics replicas could one day split the
work) that reads events via `XREADGROUP`, writes them to Postgres exactly as it does today, and
`XACK`s only after a successful write. The existing `POST /events`/`/events/batch` HTTP endpoints
stay as-is for direct/manual event submission — both paths coexist, demonstrating that async
internal instrumentation and a synchronous public API aren't mutually exclusive designs.

## Service boundary

Redis is a dependency of `api` (rate limiting, caching, stream producer) and `analytics` (rate
limiting, stream consumer) — no new deployable image, just a new official-image service in
`docker-compose.yml` and a `redis` Python client added to both services' requirements files.

## Phased roadmap

- **v1 (this phase)**: all three use cases above, verified independently.
- ~~**v2, only if actually needed**: active cache invalidation...~~ **Done (Phase 25)** — see
  "v2 hardening" below. Splitting cache/broker onto separate Redis instances remains open, only if
  one instance's load ever actually becomes a bottleneck.

## v2 hardening (Phase 25)

**Active cache invalidation, scoped to the one real mutation path that exists**: there is still no
candidate/job *profile* mutation endpoint anywhere in this API (extraction's `/extract` is
read-only; data only changes via the seed generator or `embeddings-compute`) — so "invalidate on
profile change" can't be built against a trigger that doesn't exist, honestly, not papered over.
What *does* change a candidate's job-matches ranking today is `embeddings-compute` recomputing
their embeddings (Phase 22 made `mode=blended` the default, which depends on them). `app/embeddings/
cache_invalidation.py` implements a **version-counter** scheme rather than a `SCAN`/pattern-delete
sweep: each candidate's cache key is stamped with a version (`GET
jme:cache:version:candidate:{id}`, default 0); invalidation is a single `INCR` — O(1), no blocking
scan over the keyspace — and the old versioned entries simply age out via their own TTL instead of
needing to be found and deleted. Verified live: computed embeddings for a candidate with none
before, confirmed the version key went `None -> 1`, and the identical request that previously
returned a cached `rule_based_no_embeddings` MISS-then-HIT pair came back as a **fresh MISS,
`blended`** — the stale fallback result never stayed queryable past the moment it became wrong, not
just "after up to an hour."

**Consumer-group retry/dead-letter handling**: `app/analytics/stream_consumer.py` now calls
`XPENDING`/`XCLAIM` each loop iteration to find messages idle longer than 30s (claimed by some
consumer — possibly one that crashed mid-processing — and never acked). A message under 3 delivery
attempts is reclaimed and retried; at or beyond 3, it's moved to a `:dead-letter` stream instead of
blocking the main stream forever. Verified live by deliberately simulating a crash: `XADD`'d a
message, read it with a throwaway consumer that never acked it (standing in for a process that died
mid-handling), then confirmed the real background consumer reclaimed it ~30s later via `XCLAIM`,
processed it, and the row landed correctly in Postgres — `XPENDING` showed zero entries afterward,
dead-letter stream untouched (correctly, since it succeeded within the retry budget).

## Known limitations (v2, by design)

- Single Redis instance, no Sentinel/Cluster HA — acceptable for a single-developer local
  platform, a real gap for anything beyond it.
- Cache invalidation only covers the candidate-embeddings trigger above — there's still no
  candidate/job profile mutation endpoint to invalidate against; TTL remains the only staleness
  bound for any other kind of change. A job's own embedding changing doesn't invalidate any
  candidate's cache either — no traceable "which candidates cached this job" index exists, a real,
  documented gap, not an oversight.
- Dead-letter messages are appended to a stream, not alerted on or automatically replayed — a human
  (or a future tool) has to go look. Splitting cache/broker onto separate Redis instances remains
  open, only if one instance's load ever actually becomes a bottleneck.

## Verified against real execution

Built against the real stack (`db`, `redis`, `api`, `analytics`, all actual containers — nothing
mocked), using the exact before/after discipline every prior phase has used.

**Use case 1 — rate limiting.** Re-ran Phase 18's exact test (150 rapid concurrent requests, one
API key, `ThreadPoolExecutor(max_workers=20)` against `/candidates/1/job-matches`) against the
Redis-backed limiter (`app/api/rate_limit.py`'s `storage_uri=REDIS_URL`): **100 requests succeeded,
50 got 429s** — a clean, exact split across `api`'s 4 uvicorn worker processes, replacing Phase
18's measured ~122-through-before-429s inflation. Confirmed this isn't a per-service bucket
collision risk despite `api` and `analytics` sharing one Redis instance: `slowapi` scopes its Redis
key by `(key_func_result, endpoint_identity)`, and the two services' endpoint functions are never
named the same — verified directly by instantiating `limits.storage.RedisStorage` and inspecting
the actual Redis keys it writes (`LIMITS:LIMITER/<key>/<endpoint>/100/1/minute`), not assumed.

**Use case 2 — result caching.** Called `GET /candidates/5/job-matches` twice: first response
carried `X-Cache: MISS`, second carried `X-Cache: HIT`, and `redis-cli TTL` on the stored key
confirmed it was set with ~3590 seconds remaining (a `1-hour` TTL, timing-consistent with the few
seconds the test took to run). A cached hit still re-logs a `recommendation_shown` impression
(re-showing a recommendation is still an impression, whether recomputed or not).

**Use case 3 — Redis Streams.** After a batch of matching requests, `XINFO GROUPS
analytics:recommendation_events` showed `consumers: 1`, `pending: 0`, and `entries-read` matching
`XLEN` exactly — the background consumer thread (`app/analytics/stream_consumer.py`, started from
`app/analytics/main.py`'s lifespan handler) is keeping up with zero backlog. Confirmed the events
actually land in Postgres via the stream path, not just get acknowledged: `select count(*) from
analytics.behavioral_events where source='api'` increased after each matching request, with the
correct `actor_id`/`entity_id`/`metadata` content, exactly as the old synchronous HTTP path would
have produced — proving the two paths are behaviorally equivalent from the caller's perspective,
just decoupled now.

**Fail-open design, verified by the existing test suite, not just argued.** `REDIS_URL` is unset in
the bare `pytest` run (no Redis container) — all 146 tests (6 new: `tests/api/test_cache.py`,
`tests/api/test_stream_client.py`, `tests/analytics/test_stream_consumer.py`) pass unchanged,
confirming `app/api/cache.py` and `app/api/stream_client.py` degrade to a clean miss/HTTP-fallback
rather than raising when Redis isn't configured — the same best-effort doctrine
`app/api/analytics_client.py` already used pre-Phase 21.
