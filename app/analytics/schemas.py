"""Wire schemas for the analytics ingestion API (Phase 14)."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.analytics.event_types import ACTOR_TYPES, ENTITY_TYPES, EVENT_TYPES


class EventIn(BaseModel):
    occurred_at: datetime | None = None
    actor_type: str
    actor_id: int
    session_id: str | None = None
    event_type: str
    entity_type: str | None = None
    entity_id: int | None = None
    source: str = "api"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("actor_type")
    @classmethod
    def _validate_actor_type(cls, v: str) -> str:
        if v not in ACTOR_TYPES:
            raise ValueError(f"actor_type must be one of {sorted(ACTOR_TYPES)}")
        return v

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, v: str) -> str:
        if v not in EVENT_TYPES:
            raise ValueError(f"event_type must be one of {sorted(EVENT_TYPES)}")
        return v

    @field_validator("entity_type")
    @classmethod
    def _validate_entity_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")
        return v


class EventBatchIn(BaseModel):
    events: list[EventIn]


class EventOut(BaseModel):
    event_id: str
    received_at: datetime
    event_type: str

    model_config = {"from_attributes": True}
