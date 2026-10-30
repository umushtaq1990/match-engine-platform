# Redis — Rate Limiting, Result Caching, Async Streaming (Phase 21, design)

Status: v0.1 — **design only, not yet built**. Same discipline as every prior phase: written and
reviewed before any code exists.

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
- **v2, only if actually needed**: active cache invalidation on profile/job changes (not TTL-only),
  consumer-group failure handling (`XPENDING`/claim logic for a crashed consumer — not built in
  v1, a real production gap, documented not hidden), splitting cache/broker onto separate Redis
  instances if one instance's load ever actually becomes a bottleneck.

## Known limitations (v1, by design)

- Single Redis instance, no Sentinel/Cluster HA — acceptable for a single-developer local
  platform, a real gap for anything beyond it.
- Cache invalidation is TTL-only (1 hour) — no active invalidation when a candidate's profile or a
  job posting changes within that window. A documented staleness bound, not an oversight.
- Consumer-group error handling is basic (ack-after-write, no retry/dead-letter pipeline for a
  consumer that crashes mid-batch) — a real production concern, explicitly deferred to v2.
