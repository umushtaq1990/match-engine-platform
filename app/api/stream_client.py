"""Redis Streams producer (Phase 21) for the recommendation_shown
instrumentation hook — see docs/redis-infrastructure.md "Use case 3".

Replaces the synchronous HTTP POST this hook used before Phase 21 with a
near-instant `XADD`: the stream persists events (up to MAXLEN, approximate
trimming) even if the analytics service is down entirely, which is strictly
better than the old behavior (analytics down -> HTTP call fails -> event
silently dropped). Used only when REDIS_URL is configured; app/api/
analytics_client.py falls back to the original HTTP path otherwise, so a
bare local/pytest run with no Redis container behaves exactly as before.
"""
import json
import logging
import os

import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL")
STREAM_NAME = "analytics:recommendation_events"
STREAM_MAXLEN = 100_000

_client: redis.Redis | None = None
if REDIS_URL:
    _client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)


def publish_events(events: list[dict]) -> bool:
    """Returns True if the events were handed to Redis, False otherwise (the
    caller is expected to fall back to the HTTP path on False)."""
    if _client is None:
        return False
    try:
        _client.xadd(
            STREAM_NAME, {"payload": json.dumps(events, default=str)},
            maxlen=STREAM_MAXLEN, approximate=True,
        )
        return True
    except redis.RedisError:
        logger.warning("Redis XADD failed, falling back to HTTP instrumentation", exc_info=True)
        return False
