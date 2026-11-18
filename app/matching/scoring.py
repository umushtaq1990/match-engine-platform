"""Deterministic candidate-job scoring.

Pure functions only — no DB, no I/O. Operates on CandidateProfile/JobProfile
(see profiles.py).

Issue #21: scoring simplified to skills + education only. Location, salary,
and occupation (the original brief's other three weighted components) are now
filter/display-only fields (see Candidate/Job's own columns), not part of the
score — `_location_component`/`_salary_component`/`_occupation_component`
removed outright, not left as dead code; the embeddings-blended ranker
(app/embeddings/retrieval.py) made the identical change in the same pass,
since it reused these same functions. Education was previously the brief's
"qualification compatibility," documented here as explicitly NOT implemented
("no candidate qualifications/certifications table yet... its weight is
omitted rather than faked") — that gap is exactly what this change closes,
not a new one.
"""
from dataclasses import dataclass, field

from app.common.enums import DegreeLevel
from app.matching.profiles import CandidateProfile, JobProfile

# Skill stays the dominant signal (was the largest of four components at
# 0.55; now the larger of two). The exact 70/30 split is a judgment call,
# not a tuned/evaluated number — retune later if the re-measured NDCG
# (docs/architecture/data-platform/embedding-matching.md) suggests otherwise.
WEIGHTS = {
    "skill": 0.7,
    "education": 0.3,
}

# Within skill compatibility, required skills count more than preferred ones.
REQUIRED_SKILL_WEIGHT = 0.7
PREFERRED_SKILL_WEIGHT = 0.3

# A missing/unknown value is given a neutral score, not a penalty —
# consistent with the project's "not recorded != failing" rule (see
# docs/data-model.md on CandidateSkill vs CandidateSkillAssessment).
UNKNOWN_DATA_SCORE = 0.5

# Ordinal ranking for the "candidate meets or exceeds the job's minimum
# degree level" comparison — DegreeLevel itself is a plain str enum (see
# app.common.enums), not an IntEnum, so the ordering lives here instead.
_DEGREE_LEVEL_RANK = {
    DegreeLevel.high_school: 0,
    DegreeLevel.associate: 1,
    DegreeLevel.bachelor: 2,
    DegreeLevel.master: 3,
    DegreeLevel.doctorate: 4,
}


@dataclass
class ScoreBreakdown:
    """The result of `score_candidate_job`: both the final weighted score
    and enough detail (which skills matched/were missing) to render a
    human-readable explanation without recomputing anything.

    Attributes:
        skill: the 0.0-1.0 skill-component score (see `_skill_component`).
        education: the 0.0/0.5/1.0 education-component score (see
            `_education_component`).
        total: `WEIGHTS["skill"] * skill + WEIGHTS["education"] * education`,
            rounded to 4 decimal places (as are `skill`/`education`).
        matching_required_skill_ids: required job skills the candidate has.
        missing_required_skill_ids: required job skills the candidate lacks.
        matching_preferred_skill_ids: preferred job skills the candidate has.
        missing_preferred_skill_ids: preferred job skills the candidate lacks.
    """

    skill: float
    education: float
    total: float
    matching_required_skill_ids: set[int] = field(default_factory=set)
    missing_required_skill_ids: set[int] = field(default_factory=set)
    matching_preferred_skill_ids: set[int] = field(default_factory=set)
    missing_preferred_skill_ids: set[int] = field(default_factory=set)


def _skill_component(candidate: CandidateProfile, job: JobProfile) -> tuple[float, dict]:
    """Scores how well a candidate's skills cover a job's required/preferred
    skills.

    Args:
        candidate: the candidate's profile (reads `skill_ids`).
        job: the job's profile (reads `required_skill_ids`/`preferred_skill_ids`).

    Returns:
        tuple[float, dict]: a `(score, detail)` pair. `score` is
            `REQUIRED_SKILL_WEIGHT * required_ratio + PREFERRED_SKILL_WEIGHT
            * preferred_ratio`, each ratio in `[0.0, 1.0]` (a job with no
            skills in that bucket is trivially satisfied, ratio `1.0`).
            `detail` has the four `*_skill_ids` keys `ScoreBreakdown` expects
            as keyword arguments.
    """
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


def _education_component(candidate: CandidateProfile, job: JobProfile) -> float:
    """1.0 if the job has no degree requirement (trivially satisfied, same
    convention as `_skill_component`'s no-required-skills case) or the
    candidate's own highest degree meets or exceeds it; UNKNOWN_DATA_SCORE
    if the job has a requirement but the candidate's degree level isn't
    recorded (neutral, not a penalty, same convention as the removed
    salary/occupation components used for missing data); 0.0 otherwise.
    Binary, not partial credit for "close but not quite" — same style the
    removed `_salary_component`/`_occupation_component` already used.
    """
    if job.min_degree_level is None:
        return 1.0
    if candidate.highest_degree_level is None:
        return UNKNOWN_DATA_SCORE
    return 1.0 if (
        _DEGREE_LEVEL_RANK[candidate.highest_degree_level] >= _DEGREE_LEVEL_RANK[job.min_degree_level]
    ) else 0.0


def score_candidate_job(candidate: CandidateProfile, job: JobProfile) -> ScoreBreakdown:
    """Scores one candidate against one job. The single entry point this
    module exists for — pure, deterministic, no DB/network access, safe to
    call in a tight loop (see `app.matching.rank` for the batched caller).

    Args:
        candidate: the candidate's profile (`app.matching.profiles.CandidateProfile`).
        job: the job's profile (`app.matching.profiles.JobProfile`).

    Returns:
        ScoreBreakdown: the weighted total plus per-component detail.
    """
    skill_score, skill_detail = _skill_component(candidate, job)
    education_score = _education_component(candidate, job)

    total = WEIGHTS["skill"] * skill_score + WEIGHTS["education"] * education_score

    return ScoreBreakdown(
        skill=round(skill_score, 4),
        education=round(education_score, 4),
        total=round(total, 4),
        **skill_detail,
    )
