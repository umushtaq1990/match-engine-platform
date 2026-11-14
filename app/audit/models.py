"""Edit-history for the two things in this app that are actually editable
post-creation: candidate details (app/api/profile_routes.py) and job details
(app/api/job_routes.py) -- issue #24. A separate module from
app/ingestion/source_models.py for the same reason app/analytics/models.py
and app/identity/models.py each stand alone: a distinct responsibility
(what changed and when) from the matching-engine data itself, not a
Candidate/Job column.
"""
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EntityChangeLog(Base):
    __tablename__ = "entity_change_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(index=True)
    entity_id: Mapped[int] = mapped_column(index=True)
    # Trusted as given by the frontend, same no-JWT-verification trust
    # boundary as every other route in this app -- nullable since not every
    # write path (e.g. the original seeded dataset) has a real caller identity.
    changed_by_keycloak_user_id: Mapped[str | None] = mapped_column(nullable=True)
    changed_at: Mapped[datetime] = mapped_column()
    # {field: {"old": ..., "new": ...}} for only the fields that actually
    # changed -- a creation is recorded the same way with "old": null for
    # every field, so history includes the initial creation too.
    changes: Mapped[dict] = mapped_column(JSONB)
