"""Identity-profile endpoints (issue #15) — a separate router from
routes.py's matching-engine endpoints since this is a distinct resource
group (who a Keycloak-authenticated user is, not candidate/job matching
data). Same X-API-Key auth as every other route here; no JWT verification
of the caller-supplied keycloak_user_id — see
docs/architecture/ingress-and-identity.md Known Limitations.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import DbSession, RequireApiKey
from app.api.schemas import ProfileCreate, ProfileOut
from app.identity.models import UserProfile

router = APIRouter(dependencies=[RequireApiKey])


@router.post("/profiles", response_model=ProfileOut, status_code=201)
def create_profile(payload: ProfileCreate, db: Session = DbSession):
    profile = UserProfile(
        keycloak_user_id=payload.keycloak_user_id,
        role=payload.role,
        full_name=payload.full_name,
        email=payload.email,
        company_name=payload.company_name,
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
