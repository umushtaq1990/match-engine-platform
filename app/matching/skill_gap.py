"""Skill-gap analysis (Use Case D).

Builds on the missing-required/preferred skill sets scoring.py already
computes, adding the distinction the brief explicitly requires: "do not
assume that missing data means a candidate lacks a skill." A missing skill is
split into two categories using CandidateSkillAssessment (see
docs/data-model.md):
  - "confirmed_absent": explicitly assessed and not demonstrated
  - "not_recorded": no evidence either way — unknown, not a confirmed gap
"""
from dataclasses import dataclass, field

from app.matching.profiles import CandidateProfile, JobProfile
from app.matching.scoring import ScoreBreakdown, score_candidate_job


@dataclass
class SkillGapReport:
    """Attributes:
    confirmed_absent_required: missing required skills the candidate was
        explicitly assessed on and did not demonstrate.
    not_recorded_required: missing required skills with no evidence
        either way — unknown, not a confirmed gap.
    confirmed_absent_preferred: same split, for preferred (not required)
        skills.
    not_recorded_preferred: same split, for preferred skills.
    """

    confirmed_absent_required: set[int] = field(default_factory=set)
    not_recorded_required: set[int] = field(default_factory=set)
    confirmed_absent_preferred: set[int] = field(default_factory=set)
    not_recorded_preferred: set[int] = field(default_factory=set)


def _split(missing_ids: set[int], candidate: CandidateProfile) -> tuple[set[int], set[int]]:
    """Splits a set of missing skill ids into confirmed-absent vs
    not-recorded, using `candidate.confirmed_absent_skill_ids` as the
    source of truth for which missing ids were actually assessed.

    Returns:
        tuple[set[int], set[int]]: `(confirmed_absent, not_recorded)`,
            partitioning `missing_ids` with no overlap between the two.
    """
    confirmed_absent = missing_ids & candidate.confirmed_absent_skill_ids
    not_recorded = missing_ids - confirmed_absent
    return confirmed_absent, not_recorded


def analyze_skill_gap(
    candidate: CandidateProfile, job: JobProfile, breakdown: ScoreBreakdown | None = None
) -> SkillGapReport:
    """Classifies a candidate's missing skills for a job into
    confirmed-absent vs not-recorded, for both required and preferred
    skills.

    Args:
        candidate: the candidate's profile.
        job: the job's profile.
        breakdown: an already-computed `ScoreBreakdown` to reuse (avoids
            rescoring if the caller already has one); if `None`,
            `score_candidate_job` is called to get one.

    Returns:
        SkillGapReport: the four-way classification of missing skills.
    """
    breakdown = breakdown or score_candidate_job(candidate, job)

    required_absent, required_unknown = _split(breakdown.missing_required_skill_ids, candidate)
    preferred_absent, preferred_unknown = _split(breakdown.missing_preferred_skill_ids, candidate)

    return SkillGapReport(
        confirmed_absent_required=required_absent,
        not_recorded_required=required_unknown,
        confirmed_absent_preferred=preferred_absent,
        not_recorded_preferred=preferred_unknown,
    )
