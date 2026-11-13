"""User profile storage (issue #15) — separate from app/ingestion/source_models.py's
candidate/job matching-engine tables, same rationale as app/ingestion/quality_models.py:
a distinct responsibility (who a Keycloak-authenticated user is) from the matching
data those tables hold. Not a child of Candidate/Job: an employer has no natural
table to attach to (no `employers` table exists), so this stands alone, keyed on
the Keycloak ID token's `sub` claim.
"""
import enum
from datetime import datetime

from sqlalchemy import Enum, String
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserRole(str, enum.Enum):
    candidate = "candidate"
    employer = "employer"


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    keycloak_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole))
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(255))
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
