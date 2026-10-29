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
    confirmed_absent_required: set[int] = field(default_factory=set)
    not_recorded_required: set[int] = field(default_factory=set)
    confirmed_absent_preferred: set[int] = field(default_factory=set)
    not_recorded_preferred: set[int] = field(default_factory=set)


def _split(missing_ids: set[int], candidate: CandidateProfile) -> tuple[set[int], set[int]]:
    confirmed_absent = missing_ids & candidate.confirmed_absent_skill_ids
    not_recorded = missing_ids - confirmed_absent
    return confirmed_absent, not_recorded


def analyze_skill_gap(
    candidate: CandidateProfile, job: JobProfile, breakdown: ScoreBreakdown | None = None
) -> SkillGapReport:
    breakdown = breakdown or score_candidate_job(candidate, job)

    required_absent, required_unknown = _split(breakdown.missing_required_skill_ids, candidate)
    preferred_absent, preferred_unknown = _split(breakdown.missing_preferred_skill_ids, candidate)

    return SkillGapReport(
        confirmed_absent_required=required_absent,
        not_recorded_required=required_unknown,
        confirmed_absent_preferred=preferred_absent,
        not_recorded_preferred=preferred_unknown,
    )
