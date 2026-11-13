"""Read endpoint for the edit-history audit trail (issue #24) -- a separate
router from profile_routes.py/job_routes.py since this is a cross-cutting
concern over both (candidate details, job details), not owned by either.
"""
from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.api.deps import DbSession, RequireApiKey
from app.api.schemas import ChangeLogEntryOut
from app.audit.models import EntityChangeLog

router = APIRouter(dependencies=[RequireApiKey])


@router.get("/audit/{entity_type}/{entity_id}", response_model=list[ChangeLogEntryOut])
def get_change_log(entity_type: str, entity_id: int, db: Session = DbSession):
    rows = (
        db.query(EntityChangeLog)
        .filter_by(entity_type=entity_type, entity_id=entity_id)
        .order_by(EntityChangeLog.changed_at.desc())
        .all()
    )
    return [
        ChangeLogEntryOut(
            changed_by_keycloak_user_id=row.changed_by_keycloak_user_id,
            changed_at=row.changed_at.isoformat(),
            changes=row.changes,
        )
        for row in rows
    ]
