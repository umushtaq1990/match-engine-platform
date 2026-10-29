"""Deterministic candidate-job scoring.

Pure functions only — no DB, no I/O. Operates on CandidateProfile/JobProfile
(see profiles.py). This is the documented, provisional baseline the brief
calls for: "start with documented provisional weights... do not invent final
weights and present them as objectively correct." Revisit once Phase 4
evaluation exists to compare alternatives.

Qualification compatibility (listed in the brief's original scoring formula)
is NOT implemented: there is no candidate qualifications/certifications table
yet (see docs/data-model.md known limitations). Its weight is omitted rather
than faked with a placeholder value — the remaining weights are normalized to
sum to 1.0 over the four components that are actually implemented.
"""
from dataclasses import dataclass, field

from app.matching.profiles import CandidateProfile, JobProfile

# Provisional weights (sum to 1.0). Documented starting point, not tuned.
WEIGHTS = {
    "skill": 0.55,
    "location": 0.15,
    "salary": 0.15,
    "occupation": 0.15,
}

# Within skill compatibility, required skills count more than preferred ones.
REQUIRED_SKILL_WEIGHT = 0.7
PREFERRED_SKILL_WEIGHT = 0.3

# Salary/location are given a neutral score, not a penalty, when data is
# missing — consistent with the project's "not recorded != failing" rule
# (see docs/data-model.md on CandidateSkill vs CandidateSkillAssessment).
UNKNOWN_DATA_SCORE = 0.5


@dataclass
class ScoreBreakdown:
    skill: float
    location: float
    salary: float
    occupation: float
    total: float
    matching_required_skill_ids: set[int] = field(default_factory=set)
    missing_required_skill_ids: set[int] = field(default_factory=set)
    matching_preferred_skill_ids: set[int] = field(default_factory=set)
    missing_preferred_skill_ids: set[int] = field(default_factory=set)


def _skill_component(candidate: CandidateProfile, job: JobProfile) -> tuple[float, dict]:
    matching_required = candidate.skill_ids & job.required_skill_ids
    missing_required = job.required_skill_ids - candidate.skill_ids
    matching_preferred = candidate.skill_ids & job.preferred_skill_ids
    missing_preferred = job.preferred_skill_ids - candidate.skill_ids

    required_ratio = (
        len(matching_required) / len(job.required_skill_ids)
        if job.required_skill_ids else 1.0  # no required skills -> trivially satisfied
    )
    preferred_ratio = (
        len(matching_preferred) / len(job.preferred_skill_ids)
        if job.preferred_skill_ids else 1.0
    )
    score = REQUIRED_SKILL_WEIGHT * required_ratio + PREFERRED_SKILL_WEIGHT * preferred_ratio

    return score, {
        "matching_required_skill_ids": matching_required,
        "missing_required_skill_ids": missing_required,
        "matching_preferred_skill_ids": matching_preferred,
        "missing_preferred_skill_ids": missing_preferred,
    }


def _location_component(candidate: CandidateProfile, job: JobProfile) -> float:
    if candidate.location_city == job.location_city and candidate.location_city:
        return 1.0
    if candidate.location_region == job.location_region and candidate.location_region:
        return 0.6
    return 0.0


def _salary_component(candidate: CandidateProfile, job: JobProfile) -> float:
    if (
        candidate.desired_salary_min is None or candidate.desired_salary_max is None
        or job.salary_min is None or job.salary_max is None
    ):
        return UNKNOWN_DATA_SCORE
    overlap = (
        min(candidate.desired_salary_max, job.salary_max)
        - max(candidate.desired_salary_min, job.salary_min)
    )
    return 1.0 if overlap >= 0 else 0.0


def _occupation_component(candidate: CandidateProfile, job: JobProfile) -> float:
    if candidate.primary_occupation_id is None or job.occupation_id is None:
        return UNKNOWN_DATA_SCORE
    return 1.0 if candidate.primary_occupation_id == job.occupation_id else 0.0


def score_candidate_job(candidate: CandidateProfile, job: JobProfile) -> ScoreBreakdown:
    skill_score, skill_detail = _skill_component(candidate, job)
    location_score = _location_component(candidate, job)
    salary_score = _salary_component(candidate, job)
    occupation_score = _occupation_component(candidate, job)

    total = (
        WEIGHTS["skill"] * skill_score
        + WEIGHTS["location"] * location_score
        + WEIGHTS["salary"] * salary_score
        + WEIGHTS["occupation"] * occupation_score
    )

    return ScoreBreakdown(
        skill=round(skill_score, 4),
        location=round(location_score, 4),
        salary=round(salary_score, 4),
        occupation=round(occupation_score, 4),
        total=round(total, 4),
        **skill_detail,
    )
