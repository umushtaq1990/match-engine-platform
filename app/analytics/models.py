"""ORM model for behavioral/engagement events (Phase 14).

Lives in its own Postgres schema ("analytics"), not "public" — a deliberate
bounded-context boundary (see docs/behavioral-analytics.md): this table is
owned by a separate service (app/analytics/), not the matching engine, even
though both currently share one Postgres instance for simplicity. actor_id/
entity_id are plain integers with no FK constraint into the matching
engine's candidates/jobs tables — keeping this schema's migrations
independent of theirs is the whole point of the separation; referential
integrity is checked by a data-quality report instead of a constraint.
"""
import uuid
from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BehavioralEvent(Base):
    __tablename__ = "behavioral_events"
    __table_args__ = (
        Index("ix_behavioral_events_actor", "actor_type", "actor_id", "received_at"),
        Index("ix_behavioral_events_type", "event_type", "received_at"),
        {"schema": "analytics"},
    )

    event_id: Mapped[str] = mapped_column(
        primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Client-supplied, may be absent; received_at (server-set) is
    # authoritative for ordering — see docs/behavioral-analytics.md.
    occurred_at: Mapped[datetime | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime] = mapped_column()
    actor_type: Mapped[str] = mapped_column()
    actor_id: Mapped[int] = mapped_column()
    session_id: Mapped[str | None] = mapped_column(nullable=True)
    # A fixed taxonomy enforced in application code (app/analytics/event_types.py),
    # not a Postgres ENUM — adding an event type is a one-line code change.
    event_type: Mapped[str] = mapped_column()
    entity_type: Mapped[str | None] = mapped_column(nullable=True)
    entity_id: Mapped[int | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(default="api")
    # Python attribute can't be named "metadata" — that's reserved by
    # SQLAlchemy's declarative Base. Column itself is still named "metadata".
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
