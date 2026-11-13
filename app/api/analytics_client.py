"""Best-effort instrumentation hook: logs `recommendation_shown` impressions
to the Phase 14 analytics service.

Why this exists at all: a click/apply has no denominator without a logged
impression, so click-through-rate — the whole point of this subsystem — is
uncomputable retroactively unless every returned recommendation is logged
the moment it's shown. See docs/behavioral-analytics.md.

Phase 21: now decoupled via Redis Streams (app/api/stream_client.py) when
REDIS_URL is configured — an `XADD` instead of a synchronous HTTP call,
durable even while the analytics service is down (docs/redis-infrastructure.md
"Use case 3"). Falls back to the original synchronous HTTP POST when Redis
isn't configured, so a bare local/pytest run behaves exactly as before
Phase 21. Either way this stays best-effort: every exception is caught and
logged, never raised — an outage in analytics or Redis must never break a
real matching response.
"""
import logging
import os

import httpx

from app.api.stream_client import publish_events

logger = logging.getLogger(__name__)

ANALYTICS_URL = os.environ.get("ANALYTICS_URL", "http://analytics:8001")
_TIMEOUT_SECONDS = 2.0
_ALGORITHM_VERSION = "rule-based-v1"


def _publish_or_post(events: list[dict]) -> None:
    """Shared tail of every instrumentation call: Redis Streams when
    configured, else a synchronous HTTP POST, always best-effort (every
    exception caught and logged, never raised).
    """
    if publish_events(events):
        return

    api_key = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
    try:
        httpx.post(
            f"{ANALYTICS_URL}/events/batch",
            json={"events": events},
            headers={"X-API-Key": api_key},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("Failed to log events to analytics service", exc_info=True)


def log_recommendations_shown(
    *, actor_type: str, actor_id: int, entity_type: str, entity_ids: list[int],
) -> None:
    if not entity_ids:
        return

    events = [
        {
            "actor_type": actor_type,
            "actor_id": actor_id,
            "event_type": "recommendation_shown",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "metadata": {"rank": rank, "algorithm_version": _ALGORITHM_VERSION},
        }
        for rank, entity_id in enumerate(entity_ids, start=1)
    ]
    _publish_or_post(events)


def log_event(
    *, actor_type: str, actor_id: int, event_type: str,
    entity_type: str | None = None, entity_id: int | None = None, metadata: dict | None = None,
) -> None:
    """Issue #24: a single-event sibling to log_recommendations_shown, for
    event types that aren't "N items shown" (login, search_performed,
    job_applied, job_saved).
    """
    _publish_or_post([{
        "actor_type": actor_type,
        "actor_id": actor_id,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metadata": metadata or {},
    }])
