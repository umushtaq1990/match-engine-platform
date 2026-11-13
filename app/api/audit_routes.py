"""Read endpoint for the edit-history audit trail (issue #24) -- a separate
router from profile_routes.py/job_routes.py since this is a cross-cutting
concern over both (candidate details, job details), not owned by either.
Issue #26: real JWT verification plus strict ownership -- edit history is
private, only an entity's own owner may read it, no unowned-record bypass.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import DbSession, RequireApiKey
from app.api.jwt_auth import get_verified_claims
from app.api.ownership import candidate_owner_sub, job_owner_sub, require_owner
from app.api.schemas import ChangeLogEntryOut
from app.audit.models import EntityChangeLog

router = APIRouter(dependencies=[RequireApiKey])

_OWNER_LOOKUP = {
    "candidate_details": candidate_owner_sub,
    "job_details": job_owner_sub,
}


@router.get("/audit/{entity_type}/{entity_id}", response_model=list[ChangeLogEntryOut])
def get_change_log(
    entity_type: str, entity_id: int, db: Session = DbSession, claims: dict = Depends(get_verified_claims),
):
    owner_lookup = _OWNER_LOOKUP.get(entity_type)
    if owner_lookup is None:
        raise HTTPException(status_code=404, detail="Unknown entity type")
    require_owner(owner_lookup(db, entity_id), claims["sub"], allow_unowned=False)

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
