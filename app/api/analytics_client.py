"""Best-effort instrumentation hook: logs `recommendation_shown` impressions
to the Phase 14 analytics service.

Why this exists at all: a click/apply has no denominator without a logged
impression, so click-through-rate — the whole point of this subsystem — is
uncomputable retroactively unless every returned recommendation is logged
the moment it's shown. See docs/behavioral-analytics.md.

v1 is synchronous and best-effort, by design: the analytics service is a
separate, independently deployable service (docs/behavioral-analytics.md's
service-boundary decision), so an outage there must never break a real
matching response. Every exception is caught and logged, never raised.
Making this properly non-blocking (background task or queue) is a
documented future step (v4 in the roadmap), not built now.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

ANALYTICS_URL = os.environ.get("ANALYTICS_URL", "http://analytics:8001")
_TIMEOUT_SECONDS = 2.0
_ALGORITHM_VERSION = "rule-based-v1"


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
    api_key = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
    try:
        httpx.post(
            f"{ANALYTICS_URL}/events/batch",
            json={"events": events},
            headers={"X-API-Key": api_key},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning(
            "Failed to log recommendation_shown events to analytics service", exc_info=True
        )
