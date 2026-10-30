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
from app.analytics.schemas import EventBatchIn, EventIn, EventOut

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
def ingest_event(event: EventIn, db: Session = DbSession):
    row = _to_row(event)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/events/batch", response_model=list[EventOut], status_code=201)
def ingest_events_batch(batch: EventBatchIn, db: Session = DbSession):
    """Used by the matching API's instrumentation hook, so N returned
    recommendation items cost one HTTP call, not N — see
    app/api/routes.py's `_log_recommendations_shown`.
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
