"""Employer-owned job-posting endpoints (issue #22) -- a separate router
from routes.py's matching-engine endpoints and profile_routes.py's identity
endpoints, since this is a distinct resource group (jobs an employer
themself created/edits, not matching-engine read endpoints or who a
Keycloak-authenticated user is). Same X-API-Key auth as every other route
here. Ownership is tracked by Job.posted_by_keycloak_user_id -- trusted as
given by the frontend, same no-JWT-verification trust boundary as
app/api/profile_routes.py's candidate-details endpoints, see
docs/architecture/ingress-and-identity.md Known Limitations.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.analytics_client import log_event
from app.api.deps import DbSession, RequireApiKey
from app.api.experience_levels import EXPERIENCE_LEVEL_TO_YEARS, years_experience_to_level
from app.api.schemas import (
    JobCreate,
    JobDetailsOut,
    JobDetailsUpdate,
    JobInteractionOut,
    JobListingOut,
)
from app.audit.change_log import record_change
from app.ingestion.source_models import (
    Candidate,
    CandidateJobInteraction,
    EmploymentType,
    InteractionType,
    Job,
    JobSkill,
    Occupation,
    RequirementLevel,
)

router = APIRouter(dependencies=[RequireApiKey])


def _job_audit_fields(job: Job) -> dict:
    return {
        "title": job.title, "company_name": job.company_name, "description": job.description_raw,
        "location_city": job.location_city, "postal_code": job.postal_code, "country": job.country,
        "salary_min": job.salary_min, "salary_max": job.salary_max,
        "occupation_id": job.occupation_id, "min_years_experience": job.min_years_experience,
    }


def _job_skill_ids(db: Session, job_id: int, requirement_level: RequirementLevel) -> list[int]:
    return [
        row.skill_id for row in
        db.query(JobSkill).filter_by(job_id=job_id, requirement_level=requirement_level)
    ]


def _replace_job_skills(
    db: Session, job_id: int, required_skill_ids: list[int], preferred_skill_ids: list[int],
) -> None:
    """A skill picked as both required and preferred would otherwise violate
    job_skills' (job_id, skill_id) unique constraint -- required wins, same
    as a candidate only ever getting one proficiency per skill.
    """
    db.query(JobSkill).filter_by(job_id=job_id).delete()
    for skill_id in required_skill_ids:
        db.add(JobSkill(job_id=job_id, skill_id=skill_id, requirement_level=RequirementLevel.required))
    for skill_id in preferred_skill_ids:
        if skill_id in required_skill_ids:
            continue
        db.add(JobSkill(job_id=job_id, skill_id=skill_id, requirement_level=RequirementLevel.preferred))


def _to_job_details_out(db: Session, job: Job) -> JobDetailsOut:
    occupation = db.get(Occupation, job.occupation_id) if job.occupation_id is not None else None
    return JobDetailsOut(
        id=job.id,
        title=job.title,
        company_name=job.company_name,
        description=job.description_raw,
        location_city=job.location_city,
        postal_code=job.postal_code,
        country=job.country,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        primary_occupation_id=job.occupation_id,
        occupation_title=occupation.title if occupation is not None else None,
        experience_level=(
            years_experience_to_level(job.min_years_experience) if job.min_years_experience is not None else None
        ),
        required_skill_ids=_job_skill_ids(db, job.id, RequirementLevel.required),
        preferred_skill_ids=_job_skill_ids(db, job.id, RequirementLevel.preferred),
    )


@router.post("/jobs", response_model=JobDetailsOut, status_code=201)
def create_job(payload: JobCreate, db: Session = DbSession):
    job = Job(
        title=payload.title,
        company_name=payload.company_name,
        description_raw=payload.description,
        location_city=payload.location_city,
        # Issue #23: not collected by the form -- same "Unknown" placeholder
        # convention as _get_or_create_candidate in profile_routes.py.
        location_region="Unknown",
        postal_code=payload.postal_code,
        country=payload.country,
        salary_min=payload.salary_min,
        salary_max=payload.salary_max,
        occupation_id=payload.primary_occupation_id,
        employment_type=EmploymentType.full_time,
        posted_at=datetime.now(timezone.utc),
        posted_by_keycloak_user_id=payload.posted_by_keycloak_user_id,
        min_years_experience=(
            EXPERIENCE_LEVEL_TO_YEARS[payload.experience_level] if payload.experience_level else None
        ),
    )
    db.add(job)
    db.flush()
    _replace_job_skills(db, job.id, payload.required_skill_ids, payload.preferred_skill_ids)
    record_change(
        db, "job_details", job.id, payload.posted_by_keycloak_user_id,
        old={}, new=_job_audit_fields(job),
    )
    db.commit()
    return _to_job_details_out(db, job)


@router.get("/employers/{keycloak_user_id}/jobs", response_model=list[JobListingOut])
def list_employer_jobs(keycloak_user_id: str, db: Session = DbSession):
    jobs = (
        db.query(Job).filter_by(posted_by_keycloak_user_id=keycloak_user_id)
        .order_by(Job.posted_at.desc()).all()
    )
    return [
        JobListingOut(
            id=j.id, title=j.title, company_name=j.company_name,
            location_city=j.location_city, posted_at=j.posted_at.isoformat(),
        )
        for j in jobs
    ]


@router.get("/jobs/{job_id}/details", response_model=JobDetailsOut)
def get_job_details(job_id: int, viewed_by_candidate_id: int | None = None, db: Session = DbSession):
    """Issue #25: `viewed_by_candidate_id` is only ever set by a candidate
    opening a specific match's details from their results, not by the
    employer's own edit-form load (same endpoint, different caller) --
    when present, fires `recommendation_clicked`, the one event type issue
    #24 left unemitted (no UI distinguished "opened this one result" from
    "saw the whole results table" until now).
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    if viewed_by_candidate_id is not None:
        log_event(
            actor_type="candidate", actor_id=viewed_by_candidate_id,
            event_type="recommendation_clicked", entity_type="job", entity_id=job_id,
        )
    return _to_job_details_out(db, job)


@router.patch("/jobs/{job_id}/details", response_model=JobDetailsOut)
def update_job_details(job_id: int, payload: JobDetailsUpdate, db: Session = DbSession):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")

    old_values = _job_audit_fields(job)

    job.title = payload.title
    job.company_name = payload.company_name
    job.description_raw = payload.description
    job.location_city = payload.location_city
    job.postal_code = payload.postal_code
    job.country = payload.country
    job.salary_min = payload.salary_min
    job.salary_max = payload.salary_max
    job.occupation_id = payload.primary_occupation_id
    if payload.experience_level is not None:
        job.min_years_experience = EXPERIENCE_LEVEL_TO_YEARS[payload.experience_level]

    _replace_job_skills(db, job_id, payload.required_skill_ids, payload.preferred_skill_ids)
    record_change(db, "job_details", job_id, payload.changed_by_keycloak_user_id, old=old_values, new=_job_audit_fields(job))
    db.commit()
    return _to_job_details_out(db, job)


def _get_or_create_interaction(db: Session, candidate_id: int, job_id: int, interaction_type: InteractionType) -> None:
    existing = db.query(CandidateJobInteraction).filter_by(
        candidate_id=candidate_id, job_id=job_id, interaction_type=interaction_type,
    ).first()
    if existing is not None:
        return
    interaction = CandidateJobInteraction(
        candidate_id=candidate_id, job_id=job_id, interaction_type=interaction_type,
        created_at=datetime.now(timezone.utc),
    )
    db.add(interaction)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


@router.post("/candidates/{candidate_id}/jobs/{job_id}/apply", status_code=201)
def apply_to_job(candidate_id: int, job_id: int, db: Session = DbSession):
    """Issue #24: the real action the `job_applied` event type was missing
    until now -- idempotent on re-click, same unique-constraint-backed
    get-or-create pattern used throughout this app.
    """
    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(status_code=404, detail="No such candidate")
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="No such job")
    _get_or_create_interaction(db, candidate_id, job_id, InteractionType.applied)
    log_event(
        actor_type="candidate", actor_id=candidate_id, event_type="job_applied",
        entity_type="job", entity_id=job_id,
    )
    return {"status": "applied"}


@router.post("/candidates/{candidate_id}/jobs/{job_id}/save", status_code=201)
def save_job(candidate_id: int, job_id: int, db: Session = DbSession):
    """Issue #24: same as apply_to_job above, for the `job_saved` event type."""
    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(status_code=404, detail="No such candidate")
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="No such job")
    _get_or_create_interaction(db, candidate_id, job_id, InteractionType.saved)
    log_event(
        actor_type="candidate", actor_id=candidate_id, event_type="job_saved",
        entity_type="job", entity_id=job_id,
    )
    return {"status": "saved"}


@router.get("/candidates/{candidate_id}/job-interactions", response_model=list[JobInteractionOut])
def get_candidate_job_interactions(candidate_id: int, db: Session = DbSession):
    """Issue #24: a candidate's applied/saved jobs, joined with job
    title/company so the frontend doesn't need N round-trips.
    """
    rows = (
        db.query(CandidateJobInteraction, Job)
        .join(Job, Job.id == CandidateJobInteraction.job_id)
        .filter(CandidateJobInteraction.candidate_id == candidate_id)
        .order_by(CandidateJobInteraction.created_at.desc())
        .all()
    )
    return [
        JobInteractionOut(
            job_id=job.id, title=job.title, company_name=job.company_name,
            interaction_type=interaction.interaction_type.value,
            created_at=interaction.created_at.isoformat(),
        )
        for interaction, job in rows
    ]
