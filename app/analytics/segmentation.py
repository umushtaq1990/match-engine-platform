"""Phase 24: rule-based per-actor engagement segmentation — v2 of Phase 14's
roadmap (docs/behavioral-analytics.md). RFM-style features (recency,
frequency, a conversion "monetary" analog) rolled up into a label via
documented thresholds, mirroring assumption A5's "rule-based baseline
before ML" principle that already governs the matching engine itself. Real
clustering (k-means/HDBSCAN) stays v3, only if this is shown insufficient.

Uses `received_at` (server-set), not the client-supplied `occurred_at`, for
recency/frequency — the same ordering-authority rule
app/analytics/models.py's docstring already establishes.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.analytics.models import BehavioralEvent

# Rolling window for the "frequency" feature. 30 days mirrors the synthetic
# event generator's own 0-60 day backdating window (generate_synthetic_events.py)
# — wide enough to catch a real pattern, narrow enough to reflect *current*
# behavior rather than all-time history.
FREQUENCY_WINDOW_DAYS = 30

# No event in the last 30 days at all -> dormant, regardless of past volume.
# A candidate who was highly active 2 months ago but silent since is not
# usefully described as "active" today.
DORMANT_DAYS_THRESHOLD = 30

# >=5 events in the rolling window, or a >=50% shown->applied conversion
# rate, either one is enough to call an actor "active" rather than merely
# "passive" (present but low-signal). Thresholds chosen to be interpretable
# round numbers, not fit to this one synthetic dataset — the same "simple,
# documented, not over-tuned" discipline as app/matching/scoring.py's
# WEIGHTS.
ACTIVE_EVENT_COUNT_THRESHOLD = 5
ACTIVE_CONVERSION_THRESHOLD = 0.5

SEGMENT_DORMANT = "dormant"
SEGMENT_ACTIVE_SEEKER = "active_seeker"
SEGMENT_PASSIVE_SEEKER = "passive_seeker"


def compute_segment_label(
    days_since_last_event: int | None, event_count_window: int, conversion_rate: float | None,
) -> str:
    """Pure function — no DB — unit tested directly in
    tests/analytics/test_segmentation.py.
    """
    if days_since_last_event is None or days_since_last_event > DORMANT_DAYS_THRESHOLD:
        return SEGMENT_DORMANT
    is_active = (
        event_count_window >= ACTIVE_EVENT_COUNT_THRESHOLD
        or (conversion_rate is not None and conversion_rate >= ACTIVE_CONVERSION_THRESHOLD)
    )
    return SEGMENT_ACTIVE_SEEKER if is_active else SEGMENT_PASSIVE_SEEKER


def compute_actor_features(
    session: Session, actor_type: str, actor_id: int, now: datetime | None = None,
) -> dict:
    """One aggregate query (conditional `CASE WHEN` counts), not several —
    the same N+1-avoidance discipline Phase 9/20 already established
    elsewhere in this project.
    """
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=FREQUENCY_WINDOW_DAYS)

    row = session.execute(
        select(
            func.max(BehavioralEvent.received_at).label("last_event_at"),
            func.count(
                case((BehavioralEvent.received_at >= window_start, 1))
            ).label("event_count_window"),
            func.count(
                case((BehavioralEvent.event_type == "recommendation_shown", 1))
            ).label("shown_count"),
            func.count(
                case((BehavioralEvent.event_type == "job_applied", 1))
            ).label("applied_count"),
        ).where(
            BehavioralEvent.actor_type == actor_type, BehavioralEvent.actor_id == actor_id,
        )
    ).one()

    last_event_at = row.last_event_at
    if last_event_at is not None and last_event_at.tzinfo is None:
        # The `received_at` column is `timestamp without time zone`
        # (Phase 14's migration) — Postgres silently drops the tzinfo on
        # write even though every writer passes an aware UTC value, so
        # reads come back naive. Re-attach UTC rather than compare a naive
        # and an aware datetime (which raises).
        last_event_at = last_event_at.replace(tzinfo=timezone.utc)
    days_since_last_event = (now - last_event_at).days if last_event_at is not None else None
    conversion_rate = (row.applied_count / row.shown_count) if row.shown_count > 0 else None

    return {
        "days_since_last_event": days_since_last_event,
        "event_count_window": row.event_count_window,
        "shown_count": row.shown_count,
        "applied_count": row.applied_count,
        "conversion_rate": round(conversion_rate, 4) if conversion_rate is not None else None,
    }


def segment_actor(session: Session, actor_type: str, actor_id: int, now: datetime | None = None) -> dict:
    features = compute_actor_features(session, actor_type, actor_id, now=now)
    segment = compute_segment_label(
        features["days_since_last_event"], features["event_count_window"], features["conversion_rate"],
    )
    return {**features, "actor_type": actor_type, "actor_id": actor_id, "segment": segment}
