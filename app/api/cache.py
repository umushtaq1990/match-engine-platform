"""Cache-aside result caching (Phase 21) on the matching endpoints. See
docs/redis-infrastructure.md "Use case 2".

Why 1 hour: matches the "active user session" framing from the original
request — long enough that a user paging through or reloading results
doesn't recompute a full ranking against 50,000 jobs every time, short
enough that a profile/job-posting change becomes visible within a bounded
window. No active invalidation on data change in v1 — TTL expiry is the
only staleness bound, a deliberate, documented simplification.

Fail-open by design, same doctrine as app/api/analytics_client.py: a cache
read/write error (Redis down, REDIS_URL unset for bare `pytest` runs) must
never break a real matching response — it just means every request behaves
as a cache miss.
"""
import json
import logging
import os

import redis

from app.embeddings.cache_invalidation import get_candidate_cache_version

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL")
RESULT_CACHE_TTL_SECONDS = 3600

_client: redis.Redis | None = None
if REDIS_URL:
    _client = redis.Redis.from_url(
        REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5, decode_responses=True
    )


def cache_key(*parts: object) -> str:
    return "jme:cache:" + ":".join(str(p) for p in parts)


def candidate_cache_key(category: str, candidate_id: int, *parts: object) -> str:
    """Phase 25: a version-stamped cache key (see
    app/embeddings/cache_invalidation.py) — a candidate's job-matches cache
    becomes unreachable the instant their embeddings are recomputed, instead
    of only expiring after the full 1-hour TTL.
    """
    version = get_candidate_cache_version(candidate_id)
    return cache_key(category, candidate_id, f"v{version}", *parts)


def get_cached(key: str) -> list | dict | None:
    if _client is None:
        return None
    try:
        raw = _client.get(key)
    except redis.RedisError:
        logger.warning("Redis cache read failed, treating as a miss", exc_info=True)
        return None
    return json.loads(raw) if raw is not None else None


def set_cached(key: str, value: list | dict, ttl: int = RESULT_CACHE_TTL_SECONDS) -> None:
    if _client is None:
        return
    try:
        _client.set(key, json.dumps(value), ex=ttl)
    except redis.RedisError:
        logger.warning("Redis cache write failed, result will not be cached", exc_info=True)
