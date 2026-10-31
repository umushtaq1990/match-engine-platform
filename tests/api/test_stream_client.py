"""Phase 21 Redis Streams producer (app/api/stream_client.py) must fall back
cleanly — returning False, never raising — when REDIS_URL isn't configured,
so app/api/analytics_client.py knows to use its original HTTP path. See
docs/redis-infrastructure.md "Use case 3".
"""
from app.api import stream_client


def test_publish_events_returns_false_without_a_configured_client():
    assert stream_client._client is None
    assert stream_client.publish_events([{"actor_type": "candidate", "actor_id": 1}]) is False
