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
    Course,
    CourseSkill,
    DegreeLevel,
    Job,
    JobSkill,
    OccupationSkill,
    RequirementLevel,
    Skill,
)


@dataclass
class CandidateProfile:
    """Attributes:
    id: the candidate's primary key.
    full_name: display name.
    skill_ids: every skill the candidate has (regardless of proficiency).
    confirmed_absent_skill_ids: skills explicitly assessed with
        `result=absent` — a subset of "not in skill_ids", used by
        `app.matching.skill_gap` to distinguish a confirmed gap from
        unrecorded data.
    location_city: display/filter only, not scored (issue #21).
    location_region: display/filter only, not scored.
    desired_salary_min: display/filter only, not scored.
    desired_salary_max: display/filter only, not scored.
    primary_occupation_id: the candidate's target occupation, if any —
        read by `app.matching.course_recommendations`, not by scoring.
    years_experience: display/filter only, not scored.
    highest_degree_level: scored by `_education_component`; `None` means
        not recorded (treated as neutral, not failing).
    field_of_study: display/filter only, like location_city above — no
        Job-side "required field of study" exists to score it against.
    """

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
    highest_degree_level: "DegreeLevel | None" = None
    field_of_study: str | None = None


@dataclass
class JobProfile:
    """Attributes:
    id: the job's primary key.
    title: display title.
    company_name: display name.
    required_skill_ids: skills a candidate must have to be eligible
        (see `app.matching.eligibility`) and that weigh more heavily in
        `_skill_component` than preferred skills.
    preferred_skill_ids: skills that improve the score but aren't a hard
        requirement.
    location_city: display/filter only, not scored.
    location_region: display/filter only, not scored.
    salary_min: display/filter only, not scored.
    salary_max: display/filter only, not scored.
    occupation_id: display/filter only, not scored.
    min_degree_level: scored by `_education_component`; `None` means no
        requirement (trivially satisfied).
    """

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
    min_degree_level: "DegreeLevel | None" = None


def load_skill_names(session: Session) -> dict[int, str]:
    """Returns `{skill_id: name}` for every skill in the taxonomy."""
    return dict(session.execute(select(Skill.id, Skill.name)).all())


def load_candidate_profile(session: Session, candidate_id: int) -> CandidateProfile | None:
    """Loads one candidate and translates it into a `CandidateProfile`.

    3 queries (candidate row, skill ids, confirmed-absent ids) — for more
    than a handful of candidates, prefer `load_all_candidate_profiles`
    (batch-loaded, not one call per candidate).

    Args:
        session: an active SQLAlchemy session.
        candidate_id: the candidate's primary key.

    Returns:
        CandidateProfile | None: `None` if no candidate with that id exists.
    """
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
        highest_degree_level=candidate.highest_degree_level,
        field_of_study=candidate.field_of_study,
    )


def load_job_profile(session: Session, job_id: int) -> JobProfile | None:
    """Loads one job and translates it into a `JobProfile`.

    2 queries (job row, job_skills) — for more than a handful of jobs,
    prefer `load_all_job_profiles`/`load_job_profiles_by_ids` (batch-loaded).

    Args:
        session: an active SQLAlchemy session.
        job_id: the job's primary key.

    Returns:
        JobProfile | None: `None` if no job with that id exists.
    """
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
        min_degree_level=job.min_degree_level,
    )


def load_job_profiles_by_ids(session: Session, job_ids: list[int]) -> dict[int, JobProfile]:
    """Same batch-loading discipline as load_all_job_profiles (Phase 9's N+1
    fix), scoped to a specific id subset — 2 queries regardless of subset
    size, not one `load_job_profile` call per id. Added in Phase 20 after a
    real stress test found `app/embeddings/retrieval.py` was still calling
    `load_job_profile` in a per-job loop (docs/data-at-scale.md).

    Args:
        session: an active SQLAlchemy session.
        job_ids: the jobs to load; an empty list short-circuits to `{}`
            with no queries issued.

    Returns:
        dict[int, JobProfile]: keyed by job id. Ids in `job_ids` that
            don't exist are simply absent from the result, not an error.
    """
    if not job_ids:
        return {}

    jobs = session.execute(select(Job).where(Job.id.in_(job_ids))).scalars().all()

    skill_rows = session.execute(
        select(JobSkill.job_id, JobSkill.skill_id, JobSkill.requirement_level).where(
            JobSkill.job_id.in_(job_ids)
        )
    ).all()
    required_by_job: dict[int, set[int]] = {}
    preferred_by_job: dict[int, set[int]] = {}
    for job_id, skill_id, level in skill_rows:
        target = required_by_job if level == RequirementLevel.required else preferred_by_job
        target.setdefault(job_id, set()).add(skill_id)

    return {
        job.id: JobProfile(
            id=job.id,
            title=job.title,
            company_name=job.company_name,
            required_skill_ids=required_by_job.get(job.id, set()),
            preferred_skill_ids=preferred_by_job.get(job.id, set()),
            location_city=job.location_city,
            location_region=job.location_region,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            occupation_id=job.occupation_id,
            min_degree_level=job.min_degree_level,
        )
        for job in jobs
    }


def load_all_job_profiles(session: Session) -> list[JobProfile]:
    """Batch-loaded version of load_job_profile for every job (Phase 9 fix).

    Previously issued one query per job (N+1) via load_job_profile in a loop
    — fine at this dataset's size but a documented bottleneck at scale (see
    docs/matching-engine.md). Now 2 queries total regardless of job count:
    all jobs, then all job_skills, grouped in memory.
    """
    jobs = session.execute(select(Job)).scalars().all()

    skill_rows = session.execute(
        select(JobSkill.job_id, JobSkill.skill_id, JobSkill.requirement_level)
    ).all()
    required_by_job: dict[int, set[int]] = {}
    preferred_by_job: dict[int, set[int]] = {}
    for job_id, skill_id, level in skill_rows:
        target = required_by_job if level == RequirementLevel.required else preferred_by_job
        target.setdefault(job_id, set()).add(skill_id)

    return [
        JobProfile(
            id=job.id,
            title=job.title,
            company_name=job.company_name,
            required_skill_ids=required_by_job.get(job.id, set()),
            preferred_skill_ids=preferred_by_job.get(job.id, set()),
            location_city=job.location_city,
            location_region=job.location_region,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            occupation_id=job.occupation_id,
            min_degree_level=job.min_degree_level,
        )
        for job in jobs
    ]


def load_all_candidate_profiles(session: Session) -> list[CandidateProfile]:
    """Batch-loaded version of load_candidate_profile for every candidate
    (Phase 9 fix) — same rationale as load_all_job_profiles above: 3 queries
    total instead of 1+2N.
    """
    candidates = session.execute(select(Candidate)).scalars().all()

    skill_rows = session.execute(select(CandidateSkill.candidate_id, CandidateSkill.skill_id)).all()
    skills_by_candidate: dict[int, set[int]] = {}
    for candidate_id, skill_id in skill_rows:
        skills_by_candidate.setdefault(candidate_id, set()).add(skill_id)

    absent_rows = session.execute(
        select(CandidateSkillAssessment.candidate_id, CandidateSkillAssessment.skill_id).where(
            CandidateSkillAssessment.result == AssessmentResult.absent
        )
    ).all()
    absent_by_candidate: dict[int, set[int]] = {}
    for candidate_id, skill_id in absent_rows:
        absent_by_candidate.setdefault(candidate_id, set()).add(skill_id)

    return [
        CandidateProfile(
            id=c.id,
            full_name=c.full_name,
            skill_ids=skills_by_candidate.get(c.id, set()),
            confirmed_absent_skill_ids=absent_by_candidate.get(c.id, set()),
            location_city=c.location_city,
            location_region=c.location_region,
            desired_salary_min=c.desired_salary_min,
            desired_salary_max=c.desired_salary_max,
            primary_occupation_id=c.primary_occupation_id,
            years_experience=c.years_experience,
            highest_degree_level=c.highest_degree_level,
            field_of_study=c.field_of_study,
        )
        for c in candidates
    ]


def load_occupation_skill_profile(session: Session, occupation_id: int) -> dict[int, float]:
    """Returns `{skill_id: importance}` for one occupation, the
    importance-weighted skill profile
    `app.matching.course_recommendations.analyze_occupation_skill_gap`
    compares a candidate's skills against.

    Args:
        session: an active SQLAlchemy session.
        occupation_id: the occupation's primary key.

    Returns:
        dict[int, float]: empty if the occupation has no `OccupationSkill`
            rows (not an error — just an occupation with no profile yet).
    """
    rows = session.execute(
        select(OccupationSkill.skill_id, OccupationSkill.importance).where(
            OccupationSkill.occupation_id == occupation_id
        )
    ).all()
    return {skill_id: float(importance) for skill_id, importance in rows}


def load_course_skill_coverage(session: Session) -> dict[int, dict[int, float]]:
    """course_id -> {skill_id: coverage_weight}, for every course."""
    rows = session.execute(
        select(CourseSkill.course_id, CourseSkill.skill_id, CourseSkill.coverage_weight)
    ).all()
    coverage: dict[int, dict[int, float]] = {}
    for course_id, skill_id, weight in rows:
        coverage.setdefault(course_id, {})[skill_id] = float(weight)
    return coverage


def load_course_titles(session: Session) -> dict[int, str]:
    """Returns `{course_id: title}` for every course."""
    return dict(session.execute(select(Course.id, Course.title)).all())
