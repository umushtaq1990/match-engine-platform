"""Event ingestion/query endpoints (Phase 14) — thin HTTP layer over the
behavioral_events table. No segmentation/clustering logic here yet — see
docs/behavioral-analytics.md's phased roadmap (v2/v3).
"""
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.deps import DbSession, RequireApiKey
from app.analytics.models import BehavioralEvent
from app.analytics.rate_limit import limiter
from app.analytics.schemas import ActorSegmentOut, EventBatchIn, EventIn, EventOut
from app.analytics.segmentation import segment_actor

router = APIRouter(dependencies=[RequireApiKey])


def _to_row(event: EventIn) -> BehavioralEvent:
    return BehavioralEvent(
        occurred_at=event.occurred_at,
        received_at=datetime.now(timezone.utc),
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        session_id=event.session_id,
        event_type=event.event_type,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        source=event.source,
        metadata_=event.metadata,
    )


def _to_out(row: BehavioralEvent) -> EventOut:
    return EventOut(event_id=row.event_id, received_at=row.received_at, event_type=row.event_type)


@router.post("/events", response_model=EventOut, status_code=201)
@limiter.exempt
def ingest_event(event: EventIn, db: Session = DbSession):
    row = _to_row(event)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/events/batch", response_model=list[EventOut], status_code=201)
@limiter.exempt
def ingest_events_batch(batch: EventBatchIn, db: Session = DbSession):
    """Used by the matching API's instrumentation hook, so N returned
    recommendation items cost one HTTP call, not N — see
    app/api/routes.py's `_log_recommendations_shown`.

    Exempt from rate limiting (Phase 18): this is internal, best-effort
    instrumentation traffic driven 1:1 by matching-API request volume, not
    external caller behavior — a real soak test measured ~2,280 instrumentation
    calls/minute under moderate matching-API load, which would blow past any
    reasonable numeric limit and silently drop events (this endpoint doesn't
    raise on failure by the caller's design — see app/api/analytics_client.py).
    Rate limiting matters for caller-facing endpoints, not this one.
    """
    rows = [_to_row(e) for e in batch.events]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return [_to_out(row) for row in rows]


@router.get("/actors/{actor_type}/{actor_id}/events", response_model=list[EventOut])
def get_actor_events(
    actor_type: str,
    actor_id: int,
    event_type: str | None = None,
    limit: int = 100,
    db: Session = DbSession,
):
    """Raw, paginated per-actor timeline — debugging/demo use, not a product endpoint."""
    stmt = select(BehavioralEvent).where(
        BehavioralEvent.actor_type == actor_type, BehavioralEvent.actor_id == actor_id
    )
    if event_type is not None:
        stmt = stmt.where(BehavioralEvent.event_type == event_type)
    stmt = stmt.order_by(BehavioralEvent.received_at.desc()).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [_to_out(row) for row in rows]


@router.get("/actors/{actor_type}/{actor_id}/segment", response_model=ActorSegmentOut)
def get_actor_segment(actor_type: str, actor_id: int, db: Session = DbSession):
    """Phase 24 (v2 of the behavioral-analytics roadmap): rule-based RFM-style
    engagement segmentation — active_seeker / passive_seeker / dormant — from
    recency, rolling-window frequency, and shown->applied conversion rate.
    See app/analytics/segmentation.py and docs/behavioral-analytics.md.
    """
    return segment_actor(db, actor_type, actor_id)
