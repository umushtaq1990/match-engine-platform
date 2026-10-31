"""Phase 21 cache-aside helpers (app/api/cache.py) must fail open: a cache
read/write error, or REDIS_URL simply not being configured (the case in a
bare `pytest` run with no Redis container), must behave as a clean cache
miss, never raise. See docs/redis-infrastructure.md "Use case 2".
"""
from app.api import cache


def test_cache_key_is_stable_and_joins_parts():
    assert cache.cache_key("job-matches", 1, 10, False) == "jme:cache:job-matches:1:10:False"


def test_get_cached_is_a_miss_without_a_configured_client():
    assert cache._client is None
    assert cache.get_cached(cache.cache_key("job-matches", 1, 10, False)) is None


def test_set_cached_is_a_noop_without_a_configured_client():
    # Would raise (AttributeError on a None client) if this didn't short-circuit.
    cache.set_cached(cache.cache_key("job-matches", 1, 10, False), [{"entity_id": 1}])
