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
from app.api.schemas import (
    CandidateDetailsOut,
    CandidateDetailsUpdate,
    OccupationOut,
    ProfileCreate,
    ProfileOut,
    SkillOut,
)
from app.identity.models import UserProfile, UserRole
from app.ingestion.source_models import Candidate, CandidateSkill, EvidenceSource, Occupation, ProficiencyLevel, Skill

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


@router.get("/candidates/{candidate_id}/details", response_model=CandidateDetailsOut)
def get_candidate_details(candidate_id: int, db: Session = DbSession):
    """Issue #18: the scoring-relevant fields (location/salary/occupation/
    skills) `app/matching/scoring.py` reads for this candidate.
    """
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="No such candidate")
    skill_ids = [row.skill_id for row in db.query(CandidateSkill).filter_by(candidate_id=candidate_id)]
    return CandidateDetailsOut(
        location_city=candidate.location_city,
        location_region=candidate.location_region,
        desired_salary_min=candidate.desired_salary_min,
        desired_salary_max=candidate.desired_salary_max,
        primary_occupation_id=candidate.primary_occupation_id,
        skill_ids=skill_ids,
    )


@router.patch("/candidates/{candidate_id}/details", response_model=CandidateDetailsOut)
def update_candidate_details(candidate_id: int, payload: CandidateDetailsUpdate, db: Session = DbSession):
    """Issue #18: lets a candidate set the fields the matching engine scores
    on (location/salary/occupation/skills) — previously only ever set to MVP
    placeholders at registration (issue #17). Skills are self-reported here,
    so every row gets `proficiency=intermediate`/`evidence_source=self_reported`
    — there's no UI yet to capture a finer-grained proficiency, a documented
    MVP simplification.
    """
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="No such candidate")

    candidate.location_city = payload.location_city
    candidate.location_region = payload.location_region
    candidate.desired_salary_min = payload.desired_salary_min
    candidate.desired_salary_max = payload.desired_salary_max
    candidate.primary_occupation_id = payload.primary_occupation_id

    db.query(CandidateSkill).filter_by(candidate_id=candidate_id).delete()
    for skill_id in payload.skill_ids:
        db.add(
            CandidateSkill(
                candidate_id=candidate_id, skill_id=skill_id,
                proficiency=ProficiencyLevel.intermediate, evidence_source=EvidenceSource.self_reported,
            )
        )
    db.commit()

    return CandidateDetailsOut(
        location_city=candidate.location_city,
        location_region=candidate.location_region,
        desired_salary_min=candidate.desired_salary_min,
        desired_salary_max=candidate.desired_salary_max,
        primary_occupation_id=candidate.primary_occupation_id,
        skill_ids=payload.skill_ids,
    )


@router.get("/occupations", response_model=list[OccupationOut])
def list_occupations(db: Session = DbSession):
    return db.query(Occupation).order_by(Occupation.title).all()


@router.get("/skills", response_model=list[SkillOut])
def list_skills(db: Session = DbSession):
    return db.query(Skill).order_by(Skill.name).all()
