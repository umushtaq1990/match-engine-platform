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

import redis

from app.common.config import settings

logger = logging.getLogger(__name__)

REDIS_URL = settings.redis_url

_client: redis.Redis | None = None
if REDIS_URL:
    _client = redis.Redis.from_url(
        REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5, decode_responses=True
    )


def _version_key(candidate_id: int) -> str:
    return f"jme:cache:version:candidate:{candidate_id}"


def get_candidate_cache_version(candidate_id: int) -> int:
    """Returns the current cache-version counter for a candidate — callers
    building a cache key (e.g. `app/api/cache.py`) include this so a prior
    version's entries are never read after an invalidation, even though
    they haven't expired yet.

    Fails open, same doctrine as every other Redis integration in this
    project: a read error must never break a real request, it just means
    the cache key reverts to version 0 (equivalent to "no active
    invalidation on record").

    Args:
        candidate_id: the candidate's primary key.

    Returns:
        int: the version counter (0 if never invalidated, Redis isn't
            configured, or the read failed).
    """
    if _client is None:
        return 0
    try:
        raw = _client.get(_version_key(candidate_id))
    except redis.RedisError:
        logger.warning("Redis cache-version read failed, defaulting to 0", exc_info=True)
        return 0
    return int(raw) if raw is not None else 0


def invalidate_candidate_cache(candidate_id: int) -> None:
    """Bumps a candidate's cache-version counter, invalidating every
    cache entry keyed to the prior version (see `get_candidate_cache_version`).

    A single `INCR`, not a key scan/delete — O(1) regardless of how many
    stale cache entries exist, which simply age out via their own TTL
    instead of being actively removed. Fails open: a Redis error is
    logged and swallowed, never raised.

    Args:
        candidate_id: the candidate whose cache should be invalidated.
    """
    if _client is None:
        return
    try:
        _client.incr(_version_key(candidate_id))
    except redis.RedisError:
        logger.warning(
            "Redis cache invalidation failed for candidate %s — stale cache entries will "
            "still expire via their own TTL", candidate_id, exc_info=True,
        )
