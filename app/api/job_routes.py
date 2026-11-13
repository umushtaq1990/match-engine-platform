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
from sqlalchemy.orm import Session

from app.api.deps import DbSession, RequireApiKey
from app.api.experience_levels import EXPERIENCE_LEVEL_TO_YEARS, years_experience_to_level
from app.api.schemas import JobCreate, JobDetailsOut, JobDetailsUpdate, JobListingOut
from app.ingestion.source_models import EmploymentType, Job, JobSkill, RequirementLevel

router = APIRouter(dependencies=[RequireApiKey])


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
def get_job_details(job_id: int, db: Session = DbSession):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return _to_job_details_out(db, job)


@router.patch("/jobs/{job_id}/details", response_model=JobDetailsOut)
def update_job_details(job_id: int, payload: JobDetailsUpdate, db: Session = DbSession):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")

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
    db.commit()
    return _to_job_details_out(db, job)
