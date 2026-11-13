"""Identity-profile endpoints (issue #15) — a separate router from
routes.py's matching-engine endpoints since this is a distinct resource
group (who a Keycloak-authenticated user is, not candidate/job matching
data). Same X-API-Key auth as every other route here; no JWT verification
of the caller-supplied keycloak_user_id — see
docs/architecture/ingress-and-identity.md Known Limitations.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import DbSession, RequireApiKey
from app.api.schemas import ProfileCreate, ProfileOut
from app.identity.models import UserProfile, UserRole
from app.ingestion.source_models import Candidate

router = APIRouter(dependencies=[RequireApiKey])


def _get_or_create_candidate(db: Session, full_name: str, email: str) -> int:
    """Issue #17: a real, personal `candidates` row for this user, instead
    of every candidate sharing one hardcoded candidate_id. `location_city`/
    `location_region`/`years_experience` aren't collected by the
    registration form yet, so MVP placeholders are used here — see
    docs/architecture/frontend.md Known Limitations.
    """
    candidate = Candidate(
        full_name=full_name, email=email, location_city="Unknown", location_region="Unknown",
        years_experience=0, created_at=datetime.now(timezone.utc),
    )
    db.add(candidate)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        candidate = db.query(Candidate).filter_by(email=email).first()
        if candidate is None:
            raise
    return candidate.id


@router.post("/profiles", response_model=ProfileOut, status_code=201)
def create_profile(payload: ProfileCreate, db: Session = DbSession):
    candidate_id = None
    if payload.role == UserRole.candidate:
        candidate_id = _get_or_create_candidate(db, payload.full_name, payload.email)

    profile = UserProfile(
        keycloak_user_id=payload.keycloak_user_id,
        role=payload.role,
        full_name=payload.full_name,
        email=payload.email,
        company_name=payload.company_name,
        candidate_id=candidate_id,
    )
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Profile already exists for this user")
    db.refresh(profile)
    return profile


@router.get("/profiles/{keycloak_user_id}", response_model=ProfileOut)
def get_profile(keycloak_user_id: str, db: Session = DbSession):
    profile = db.query(UserProfile).filter_by(keycloak_user_id=keycloak_user_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile for this user")
    return profile
