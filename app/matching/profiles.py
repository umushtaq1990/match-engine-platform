"""Plain data structures for matching, decoupled from the ORM.

Scoring/eligibility logic (scoring.py, eligibility.py) operates only on these
dataclasses, never on SQLAlchemy models directly — so the matching rules are
unit-testable with hand-built fixtures and no database. The functions below
are the (thin) translation layer from ORM rows to these structures.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.source_models import (
    AssessmentResult,
    Candidate,
    CandidateSkill,
    CandidateSkillAssessment,
    Job,
    JobSkill,
    RequirementLevel,
    Skill,
)


@dataclass
class CandidateProfile:
    id: int
    full_name: str
    skill_ids: set[int]
    confirmed_absent_skill_ids: set[int]  # explicitly assessed, result=absent
    location_city: str
    location_region: str
    desired_salary_min: int | None
    desired_salary_max: int | None
    primary_occupation_id: int | None
    years_experience: int


@dataclass
class JobProfile:
    id: int
    title: str
    company_name: str
    required_skill_ids: set[int] = field(default_factory=set)
    preferred_skill_ids: set[int] = field(default_factory=set)
    location_city: str = ""
    location_region: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    occupation_id: int | None = None


def load_skill_names(session: Session) -> dict[int, str]:
    return dict(session.execute(select(Skill.id, Skill.name)).all())


def load_candidate_profile(session: Session, candidate_id: int) -> CandidateProfile | None:
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        return None

    skill_ids = set(
        session.execute(
            select(CandidateSkill.skill_id).where(CandidateSkill.candidate_id == candidate_id)
        ).scalars()
    )
    confirmed_absent = set(
        session.execute(
            select(CandidateSkillAssessment.skill_id).where(
                CandidateSkillAssessment.candidate_id == candidate_id,
                CandidateSkillAssessment.result == AssessmentResult.absent,
            )
        ).scalars()
    )

    return CandidateProfile(
        id=candidate.id,
        full_name=candidate.full_name,
        skill_ids=skill_ids,
        confirmed_absent_skill_ids=confirmed_absent,
        location_city=candidate.location_city,
        location_region=candidate.location_region,
        desired_salary_min=candidate.desired_salary_min,
        desired_salary_max=candidate.desired_salary_max,
        primary_occupation_id=candidate.primary_occupation_id,
        years_experience=candidate.years_experience,
    )


def load_job_profile(session: Session, job_id: int) -> JobProfile | None:
    job = session.get(Job, job_id)
    if job is None:
        return None

    rows = session.execute(
        select(JobSkill.skill_id, JobSkill.requirement_level).where(JobSkill.job_id == job_id)
    ).all()
    required = {sid for sid, level in rows if level == RequirementLevel.required}
    preferred = {sid for sid, level in rows if level == RequirementLevel.preferred}

    return JobProfile(
        id=job.id,
        title=job.title,
        company_name=job.company_name,
        required_skill_ids=required,
        preferred_skill_ids=preferred,
        location_city=job.location_city,
        location_region=job.location_region,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        occupation_id=job.occupation_id,
    )


def load_all_job_profiles(session: Session) -> list[JobProfile]:
    job_ids = session.execute(select(Job.id)).scalars().all()
    return [load_job_profile(session, jid) for jid in job_ids]


def load_all_candidate_profiles(session: Session) -> list[CandidateProfile]:
    candidate_ids = session.execute(select(Candidate.id)).scalars().all()
    return [load_candidate_profile(session, cid) for cid in candidate_ids]
