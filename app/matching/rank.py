"""Ranking orchestration — Use Cases A (candidate->job) and B (job->candidate).

Both directions reuse the same eligibility + scoring + skill-gap logic
(score_candidate_job is symmetric in what it computes); only the "which side
varies" loop differs. This is the one module that talks to the database
directly (via profiles.py loaders), tying the pure logic above to real data.

load_all_job_profiles/load_all_candidate_profiles used to issue one query
per row (N+1 — fine at this dataset's size, but a real bottleneck at
larger scale). Fixed in Phase 9: both now batch-load in a small, fixed
number of queries regardless of row count — measured ~1.95s/candidate
down to ~0.06s/candidate for a full rank_jobs_for_candidate call. See
docs/matching-engine.md for the measured before/after.
"""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.matching.eligibility import check_eligibility
from app.matching.profiles import (
    CandidateProfile,
    JobProfile,
    load_all_candidate_profiles,
    load_all_job_profiles,
    load_candidate_profile,
    load_job_profile,
    load_skill_names,
)
from app.matching.scoring import ScoreBreakdown, score_candidate_job
from app.matching.skill_gap import SkillGapReport, analyze_skill_gap


@dataclass
class MatchResult:
    entity_id: int  # job.id when ranking jobs for a candidate, and vice versa
    label: str  # job title or candidate name, for display
    eligible: bool
    ineligible_reason: str | None
    score: ScoreBreakdown
    skill_gap: SkillGapReport
    explanation: str = field(default="")


def _explain(breakdown: ScoreBreakdown, skill_names: dict[int, str]) -> str:
    matched = sorted(skill_names.get(i, f"skill#{i}") for i in breakdown.matching_required_skill_ids)
    missing = sorted(skill_names.get(i, f"skill#{i}") for i in breakdown.missing_required_skill_ids)
    parts = [f"total score {breakdown.total:.2f} (skill {breakdown.skill:.2f}, "
             f"location {breakdown.location:.2f}, salary {breakdown.salary:.2f}, "
             f"occupation {breakdown.occupation:.2f})"]
    if matched:
        parts.append(f"matches required skills: {', '.join(matched)}")
    if missing:
        parts.append(f"missing required skills: {', '.join(missing)}")
    return "; ".join(parts)


def _build_result(
    entity_id: int, label: str, candidate: CandidateProfile, job: JobProfile,
    skill_names: dict[int, str],
) -> MatchResult:
    eligibility = check_eligibility(candidate, job)
    breakdown = score_candidate_job(candidate, job)
    gap = analyze_skill_gap(candidate, job, breakdown)
    return MatchResult(
        entity_id=entity_id,
        label=label,
        eligible=eligibility.eligible,
        ineligible_reason=eligibility.reason,
        score=breakdown,
        skill_gap=gap,
        explanation=_explain(breakdown, skill_names),
    )


def rank_jobs_for_candidate(
    session: Session, candidate_id: int, top_k: int = 10, include_ineligible: bool = False
) -> list[MatchResult]:
    candidate = load_candidate_profile(session, candidate_id)
    if candidate is None:
        raise ValueError(f"No candidate with id={candidate_id}")

    jobs = load_all_job_profiles(session)
    skill_names = load_skill_names(session)

    results = [
        _build_result(job.id, job.title, candidate, job, skill_names) for job in jobs
    ]
    if not include_ineligible:
        results = [r for r in results if r.eligible]
    results.sort(key=lambda r: r.score.total, reverse=True)
    return results[:top_k]


def rank_candidates_for_job(
    session: Session, job_id: int, top_k: int = 10, include_ineligible: bool = False
) -> list[MatchResult]:
    job = load_job_profile(session, job_id)
    if job is None:
        raise ValueError(f"No job with id={job_id}")

    candidates = load_all_candidate_profiles(session)
    skill_names = load_skill_names(session)

    results = [
        _build_result(c.id, c.full_name, c, job, skill_names) for c in candidates
    ]
    if not include_ineligible:
        results = [r for r in results if r.eligible]
    results.sort(key=lambda r: r.score.total, reverse=True)
    return results[:top_k]
