"""Phase 25: active cache invalidation for a candidate's job-matches cache,
triggered by the one real "this candidate's data changed" event that exists
today — `compute_embeddings.py` recomputing their embeddings. Closes the gap
flagged in docs/redis-infrastructure.md "Known limitations" (TTL-only
invalidation) at the one integration point this project actually has; there
is still no candidate/job *profile* mutation endpoint anywhere in the API,
so that half of the original limitation remains honestly out of scope.

Lives in app/embeddings/ (not app/api/), and is intentionally generic (no
`job-matches`-shaped key knowledge) because this module is imported from
both the `api` image and the `extraction`-based `embeddings-compute` image
(see docker-compose.yml) — `app/api/` is never imported from the extraction
family, the same service-boundary discipline as every other cross-service
Redis helper in this project. A version-counter scheme is used instead of a
Redis `SCAN`/pattern-delete sweep: invalidation is a single `INCR` (O(1), no
blocking scan over the keyspace), and old versioned entries simply age out
via their own TTL rather than needing to be found and deleted.
"""
import logging
import os

import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL")

_client: redis.Redis | None = None
if REDIS_URL:
    _client = redis.Redis.from_url(
        REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5, decode_responses=True
    )


def _version_key(candidate_id: int) -> str:
    return f"jme:cache:version:candidate:{candidate_id}"


def get_candidate_cache_version(candidate_id: int) -> int:
    if _client is None:
        return 0
    try:
        raw = _client.get(_version_key(candidate_id))
    except redis.RedisError:
        logger.warning("Redis cache-version read failed, defaulting to 0", exc_info=True)
        return 0
    return int(raw) if raw is not None else 0


def invalidate_candidate_cache(candidate_id: int) -> None:
    if _client is None:
        return
    try:
        _client.incr(_version_key(candidate_id))
    except redis.RedisError:
        logger.warning(
            "Redis cache invalidation failed for candidate %s — stale cache entries will "
            "still expire via their own TTL", candidate_id, exc_info=True,
        )
