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
    """One ranked entry: the "other side" entity scored against the fixed
    side of the call (a job, when ranking jobs for a candidate; a
    candidate, when ranking candidates for a job).

    Attributes:
        entity_id: `job.id` when ranking jobs for a candidate, and vice
            versa.
        label: job title or candidate name, for display.
        eligible: from `check_eligibility` — hard constraint, independent
            of `score`.
        ineligible_reason: human-readable reason when `eligible` is
            `False`; `None` otherwise.
        score: the full `ScoreBreakdown` from `score_candidate_job`.
        skill_gap: the `SkillGapReport` from `analyze_skill_gap`.
        explanation: a one-line human-readable summary (see `_explain`).
    """

    entity_id: int  # job.id when ranking jobs for a candidate, and vice versa
    label: str  # job title or candidate name, for display
    eligible: bool
    ineligible_reason: str | None
    score: ScoreBreakdown
    skill_gap: SkillGapReport
    explanation: str = field(default="")


def _explain(breakdown: ScoreBreakdown, skill_names: dict[int, str]) -> str:
    """Renders a `ScoreBreakdown` into a one-line human-readable summary
    (total/component scores, matched/missing required skill names).

    Args:
        breakdown: the score to explain.
        skill_names: `{skill_id: name}`, used to render skill ids as
            readable names (falling back to `f"skill#{id}"` for an id not
            in this map).

    Returns:
        str: a semicolon-joined summary, e.g. `"total score 0.79 (skill
            0.85, education 1.00); matches required skills: Python;
            missing required skills: Java"`.
    """
    matched = sorted(skill_names.get(i, f"skill#{i}") for i in breakdown.matching_required_skill_ids)
    missing = sorted(skill_names.get(i, f"skill#{i}") for i in breakdown.missing_required_skill_ids)
    parts = [f"total score {breakdown.total:.2f} (skill {breakdown.skill:.2f}, "
             f"education {breakdown.education:.2f})"]
    if matched:
        parts.append(f"matches required skills: {', '.join(matched)}")
    if missing:
        parts.append(f"missing required skills: {', '.join(missing)}")
    return "; ".join(parts)


def _build_result(
    entity_id: int, label: str, candidate: CandidateProfile, job: JobProfile,
    skill_names: dict[int, str],
) -> MatchResult:
    """Runs eligibility + scoring + skill-gap analysis for one
    candidate/job pair and assembles the result — the shared tail of both
    `rank_jobs_for_candidate` and `rank_candidates_for_job`'s per-row loop.

    Args:
        entity_id: the "other side" entity's id (see `MatchResult.entity_id`).
        label: that entity's display label.
        candidate: the candidate's profile.
        job: the job's profile.
        skill_names: `{skill_id: name}`, forwarded to `_explain`.

    Returns:
        MatchResult: eligibility, score, skill gap, and explanation, all
            computed from the same single `score_candidate_job` call.
    """
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
    session: Session, candidate_id: int, top_k: int | None = 10, include_ineligible: bool = False
) -> list[MatchResult]:
    """Ranks every job against one candidate (Use Case A).

    Args:
        session: an active SQLAlchemy session.
        candidate_id: the candidate's primary key.
        top_k: how many results to return, highest `score.total` first.
            `None` returns every eligible result, unsorted-length-wise
            unlimited (Python's `list[:None]` slicing already does the
            right thing) — used by Phase 22's blended-default
            re-ranking, which needs the full rule-based pool before
            re-sorting it by embedding similarity.
        include_ineligible: if `True`, ineligible jobs are included in
            the (still score-sorted) result instead of filtered out.

    Returns:
        list[MatchResult]: sorted by `score.total` descending.

    Raises:
        ValueError: if no candidate with `candidate_id` exists.
    """
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
    """Ranks every candidate against one job (Use Case B) — the mirror of
    `rank_jobs_for_candidate`.

    Args:
        session: an active SQLAlchemy session.
        job_id: the job's primary key.
        top_k: how many results to return, highest `score.total` first.
        include_ineligible: if `True`, ineligible candidates are included
            in the (still score-sorted) result instead of filtered out.

    Returns:
        list[MatchResult]: sorted by `score.total` descending.

    Raises:
        ValueError: if no job with `job_id` exists.
    """
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
