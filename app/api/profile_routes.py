"""Identity-profile endpoints (issue #15) — a separate router from
routes.py's matching-engine endpoints since this is a distinct resource
group (who a Keycloak-authenticated user is, not candidate/job matching
data). Same X-API-Key auth as every other route here; no JWT verification
of the caller-supplied keycloak_user_id — see
docs/architecture/ingress-and-identity.md Known Limitations.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.analytics_client import log_event
from app.api.deps import DbSession, RequireApiKey
from app.api.experience_levels import EXPERIENCE_LEVEL_TO_YEARS, years_experience_to_level
from app.api.schemas import (
    CandidateDetailsOut,
    CandidateDetailsUpdate,
    OccupationCreate,
    OccupationOut,
    ProfileCreate,
    ProfileOut,
    SkillCreate,
    SkillOut,
)
from app.audit.change_log import record_change
from app.identity.models import UserProfile, UserRole
from app.ingestion.source_models import Candidate, CandidateSkill, EvidenceSource, Occupation, ProficiencyLevel, Skill

router = APIRouter(dependencies=[RequireApiKey])


def _get_or_create_candidate(db: Session, full_name: str, email: str, changed_by: str | None) -> int:
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

    # Issue #24: only on the fresh-insert path above -- reusing an existing
    # candidate row on the IntegrityError branch isn't a creation.
    record_change(
        db, "candidate_details", candidate.id, changed_by, old={},
        new={"full_name": full_name, "email": email, "location_city": "Unknown", "years_experience": 0},
    )
    return candidate.id


@router.post("/profiles", response_model=ProfileOut, status_code=201)
def create_profile(payload: ProfileCreate, db: Session = DbSession):
    candidate_id = None
    if payload.role == UserRole.candidate:
        candidate_id = _get_or_create_candidate(
            db, payload.full_name, payload.email, payload.keycloak_user_id,
        )

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
    # Issue #24: this is a brand-new user's first login, via registration --
    # see get_profile() below for the returning-user case.
    log_event(actor_type=profile.role.value, actor_id=profile.id, event_type="login")
    return profile


@router.get("/profiles/{keycloak_user_id}", response_model=ProfileOut)
def get_profile(keycloak_user_id: str, db: Session = DbSession):
    profile = db.query(UserProfile).filter_by(keycloak_user_id=keycloak_user_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile for this user")
    # Issue #24: a returning user's login -- the frontend only calls this
    # once per real session (it caches the profile in session_state
    # afterward), so this fires once per actual login, not once per rerun.
    log_event(actor_type=profile.role.value, actor_id=profile.id, event_type="login")
    return profile


@router.get("/candidates/{candidate_id}/details", response_model=CandidateDetailsOut)
def get_candidate_details(candidate_id: int, db: Session = DbSession):
    """Issue #18/#20: location/salary/occupation/experience/skills for this
    candidate. Only skills + occupation are read by
    `app/matching/scoring.py` today; location/salary/experience are stored
    for filtering/analysis, not scored (see issue #21 for the discussion of
    simplifying scoring to skills+education only).
    """
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="No such candidate")
    skill_ids = [row.skill_id for row in db.query(CandidateSkill).filter_by(candidate_id=candidate_id)]
    return CandidateDetailsOut(
        location_city=candidate.location_city,
        postal_code=candidate.postal_code,
        country=candidate.country,
        desired_salary_min=candidate.desired_salary_min,
        desired_salary_max=candidate.desired_salary_max,
        primary_occupation_id=candidate.primary_occupation_id,
        experience_level=years_experience_to_level(candidate.years_experience),
        skill_ids=skill_ids,
    )


@router.patch("/candidates/{candidate_id}/details", response_model=CandidateDetailsOut)
def update_candidate_details(candidate_id: int, payload: CandidateDetailsUpdate, db: Session = DbSession):
    """Issue #18/#20: lets a candidate set location/salary/occupation/
    experience/skills — previously only ever set to MVP placeholders at
    registration (issue #17). `location_region` is deliberately not updated
    here (see `CandidateDetailsUpdate`'s docstring). Skills are self-reported
    here, so every row gets `proficiency=intermediate`/
    `evidence_source=self_reported` — there's no UI yet to capture a
    finer-grained proficiency, a documented MVP simplification.
    """
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="No such candidate")

    old_values = {
        "location_city": candidate.location_city, "postal_code": candidate.postal_code,
        "country": candidate.country, "desired_salary_min": candidate.desired_salary_min,
        "desired_salary_max": candidate.desired_salary_max,
        "primary_occupation_id": candidate.primary_occupation_id,
        "years_experience": candidate.years_experience,
    }

    candidate.location_city = payload.location_city
    candidate.postal_code = payload.postal_code
    candidate.country = payload.country
    candidate.desired_salary_min = payload.desired_salary_min
    candidate.desired_salary_max = payload.desired_salary_max
    candidate.primary_occupation_id = payload.primary_occupation_id
    if payload.experience_level is not None:
        candidate.years_experience = EXPERIENCE_LEVEL_TO_YEARS[payload.experience_level]

    db.query(CandidateSkill).filter_by(candidate_id=candidate_id).delete()
    for skill_id in payload.skill_ids:
        db.add(
            CandidateSkill(
                candidate_id=candidate_id, skill_id=skill_id,
                proficiency=ProficiencyLevel.intermediate, evidence_source=EvidenceSource.self_reported,
            )
        )

    record_change(
        db, "candidate_details", candidate_id, payload.changed_by_keycloak_user_id,
        old=old_values,
        new={
            "location_city": candidate.location_city, "postal_code": candidate.postal_code,
            "country": candidate.country, "desired_salary_min": candidate.desired_salary_min,
            "desired_salary_max": candidate.desired_salary_max,
            "primary_occupation_id": candidate.primary_occupation_id,
            "years_experience": candidate.years_experience,
        },
    )
    db.commit()

    return CandidateDetailsOut(
        location_city=candidate.location_city,
        postal_code=candidate.postal_code,
        country=candidate.country,
        desired_salary_min=candidate.desired_salary_min,
        desired_salary_max=candidate.desired_salary_max,
        primary_occupation_id=candidate.primary_occupation_id,
        experience_level=years_experience_to_level(candidate.years_experience),
        skill_ids=payload.skill_ids,
    )


@router.get("/occupations", response_model=list[OccupationOut])
def list_occupations(db: Session = DbSession):
    """Issue #19: only the curated titles -- the synthetic dataset's padding
    rows (is_curated=False, see the migration that added this column) are
    excluded so they don't clutter a picker.
    """
    return db.query(Occupation).filter_by(is_curated=True).order_by(Occupation.title).all()


@router.post("/occupations", response_model=OccupationOut, status_code=201)
def create_occupation(payload: OccupationCreate, response: Response, db: Session = DbSession):
    """Issue #19: lets a candidate add an occupation that isn't in the
    curated list yet. Case-insensitive get-or-create -- re-submitting an
    existing title (curated or not) returns that row instead of creating a
    duplicate, same idempotent-on-collision pattern as
    `_get_or_create_candidate` above.
    """
    existing = db.query(Occupation).filter(func.lower(Occupation.title) == payload.title.lower()).first()
    if existing is not None:
        response.status_code = 200
        return existing

    occupation = Occupation(code=f"OCC-USER-{uuid.uuid4().hex[:8]}", title=payload.title, is_curated=True)
    db.add(occupation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        occupation = db.query(Occupation).filter(func.lower(Occupation.title) == payload.title.lower()).first()
        if occupation is None:
            raise
        response.status_code = 200
    return occupation


@router.get("/skills", response_model=list[SkillOut])
def list_skills(db: Session = DbSession):
    return db.query(Skill).order_by(Skill.name).all()


@router.post("/skills", response_model=SkillOut, status_code=201)
def create_skill(payload: SkillCreate, response: Response, db: Session = DbSession):
    """Issue #19: same case-insensitive get-or-create as create_occupation
    above, for a candidate adding a skill that isn't in the list yet.
    """
    existing = db.query(Skill).filter(func.lower(Skill.name) == payload.name.lower()).first()
    if existing is not None:
        response.status_code = 200
        return existing

    skill = Skill(name=payload.name)
    db.add(skill)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        skill = db.query(Skill).filter(func.lower(Skill.name) == payload.name.lower()).first()
        if skill is None:
            raise
        response.status_code = 200
    return skill
