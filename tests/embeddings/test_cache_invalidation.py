"""Phase 25 cache-invalidation helper (app/embeddings/cache_invalidation.py)
must fail open: no REDIS_URL configured (the case in a bare `pytest` run)
behaves as version 0 / a silent no-op, never raises. See
docs/redis-infrastructure.md "Known limitations".
"""
from app.embeddings import cache_invalidation


def test_get_candidate_cache_version_defaults_to_zero_without_a_configured_client():
    assert cache_invalidation._client is None
    assert cache_invalidation.get_candidate_cache_version(1) == 0


def test_invalidate_candidate_cache_is_a_noop_without_a_configured_client():
    # Would raise (AttributeError on a None client) if this didn't short-circuit.
    cache_invalidation.invalidate_candidate_cache(1)
