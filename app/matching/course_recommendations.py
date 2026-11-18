"""Course recommendations (Use Case E) via an occupation-target skill gap.

Different from skill_gap.py's job-specific gap (used during ranking): this
gap is computed against a candidate's TARGET OCCUPATION's skill profile
(OccupationSkill, built in Phase 2 specifically for this), not against one
job posting. Approach: candidate states/has a target occupation -> compare
their skills against that occupation's importance-weighted skill profile ->
recommend courses that best cover the resulting weighted gap.

This reuses data already in the schema rather than inventing anything new:
OccupationSkill.importance *is* the "skill frequency/importance for this
occupation" table, and CourseSkill.coverage_weight already captures how
central a skill is to a course.

MVP scope, documented: ranks individual courses by how much weighted gap
each one covers alone. Does NOT solve multi-course "set cover" (the minimal
combination of courses to close the whole gap) — a reasonable Phase 4/5
improvement once this baseline is evaluated, not built here to avoid
premature complexity.
"""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.matching.profiles import (
    CandidateProfile,
    load_candidate_profile,
    load_course_skill_coverage,
    load_course_titles,
    load_occupation_skill_profile,
)


@dataclass
class OccupationSkillGapReport:
    """Attributes:
    occupation_id: the target occupation this gap was computed against.
    present: `{skill_id: importance}` for occupation skills the candidate
        already has.
    confirmed_absent: `{skill_id: importance}` for missing occupation
        skills the candidate was explicitly assessed on and lacks.
    not_recorded: `{skill_id: importance}` for missing occupation skills
        with no evidence either way.
    """

    occupation_id: int
    present: dict[int, float] = field(default_factory=dict)  # skill_id -> importance
    confirmed_absent: dict[int, float] = field(default_factory=dict)
    not_recorded: dict[int, float] = field(default_factory=dict)

    @property
    def total_gap_weight(self) -> float:
        """Sum of `confirmed_absent` and `not_recorded` importances — the
        denominator `recommend_courses` normalizes `coverage_score`
        against."""
        return sum(self.confirmed_absent.values()) + sum(self.not_recorded.values())


@dataclass
class CourseRecommendation:
    """Attributes:
    course_id: the recommended course's primary key.
    title: the course's display title.
    coverage_score: 0.0-1.0, the share of the candidate's importance-
        weighted occupation-skill gap this one course alone covers.
    covered_skill_ids: which of the gap's skill ids this course addresses.
    """

    course_id: int
    title: str
    coverage_score: float  # 0-1: share of the candidate's weighted gap this course addresses
    covered_skill_ids: set[int]


def analyze_occupation_skill_gap(
    candidate: CandidateProfile, occupation_id: int, occupation_skill_profile: dict[int, float]
) -> OccupationSkillGapReport:
    """Splits a candidate's target occupation's skill profile into skills
    they already have vs. confirmed-absent vs. not-recorded (same
    evidence distinction as `app.matching.skill_gap`, applied to an
    occupation's profile instead of one job's required/preferred skills).

    Args:
        candidate: the candidate's profile.
        occupation_id: the target occupation's id (recorded on the report
            as-is; not re-derived or validated against `candidate`).
        occupation_skill_profile: `{skill_id: importance}` for the target
            occupation, as returned by
            `app.matching.profiles.load_occupation_skill_profile`.

    Returns:
        OccupationSkillGapReport: the three-way split.
    """
    present = {sid: w for sid, w in occupation_skill_profile.items() if sid in candidate.skill_ids}
    missing = {sid: w for sid, w in occupation_skill_profile.items() if sid not in candidate.skill_ids}
    confirmed_absent = {
        sid: w for sid, w in missing.items() if sid in candidate.confirmed_absent_skill_ids
    }
    not_recorded = {sid: w for sid, w in missing.items() if sid not in confirmed_absent}

    return OccupationSkillGapReport(
        occupation_id=occupation_id,
        present=present,
        confirmed_absent=confirmed_absent,
        not_recorded=not_recorded,
    )


def recommend_courses(
    gap: OccupationSkillGapReport,
    course_skill_coverage: dict[int, dict[int, float]],  # course_id -> {skill_id: coverage_weight}
    course_titles: dict[int, str],
    top_k: int = 5,
) -> list[CourseRecommendation]:
    """Ranks courses by how much of a candidate's weighted skill gap each
    one covers alone. Pure function — no DB access; `course_skill_coverage`/
    `course_titles` are pre-loaded dicts the caller supplies (see
    `recommend_courses_for_candidate` for the DB-backed convenience
    wrapper).

    MVP scope, documented: ranks courses independently, does not solve
    multi-course set-cover (the minimal combination of courses that
    together close the whole gap) — see module docstring.

    Args:
        gap: the candidate's occupation skill gap (both `confirmed_absent`
            and `not_recorded` count toward coverage — a course closing an
            unconfirmed gap is still useful).
        course_skill_coverage: `{course_id: {skill_id: coverage_weight}}`.
        course_titles: `{course_id: title}`, used for display; a course id
            with no entry falls back to `f"course#{course_id}"`.
        top_k: maximum number of recommendations to return.

    Returns:
        list[CourseRecommendation]: up to `top_k` recommendations, sorted
            by `coverage_score` descending. Empty if the gap's total
            weight is zero (nothing to recommend against) or no course
            covers any gap skill.
    """
    gap_weights = {**gap.confirmed_absent, **gap.not_recorded}
    total_gap = sum(gap_weights.values())
    if total_gap == 0:
        return []

    recommendations = []
    for course_id, skill_coverage in course_skill_coverage.items():
        covered = set(skill_coverage) & set(gap_weights)
        if not covered:
            continue
        raw_score = sum(gap_weights[sid] * skill_coverage[sid] for sid in covered)
        recommendations.append(
            CourseRecommendation(
                course_id=course_id,
                title=course_titles.get(course_id, f"course#{course_id}"),
                coverage_score=round(raw_score / total_gap, 4),
                covered_skill_ids=covered,
            )
        )

    recommendations.sort(key=lambda r: r.coverage_score, reverse=True)
    return recommendations[:top_k]


def recommend_courses_for_candidate(
    session: Session, candidate_id: int, top_k: int = 5
) -> tuple[OccupationSkillGapReport, list[CourseRecommendation]]:
    """Convenience entry point tying the DB together for one candidate:
    loads their profile and target occupation's skill profile, computes
    the gap, and recommends courses against it — the composition of
    `analyze_occupation_skill_gap` + `recommend_courses` plus the DB
    loaders they need.

    Requires the candidate to have a primary_occupation_id (their target
    role). Candidates without one (see docs/data-model.md: ~5% of the
    synthetic dataset by design) cannot get this recommendation type yet —
    raises explicitly rather than silently returning an empty/misleading
    result.

    Args:
        session: an active SQLAlchemy session.
        candidate_id: the candidate's primary key.
        top_k: forwarded to `recommend_courses`.

    Returns:
        tuple[OccupationSkillGapReport, list[CourseRecommendation]]: the
            computed gap and its resulting course recommendations.

    Raises:
        ValueError: if no candidate with `candidate_id` exists, or they
            have no `primary_occupation_id`.
    """
    candidate = load_candidate_profile(session, candidate_id)
    if candidate is None:
        raise ValueError(f"No candidate with id={candidate_id}")
    if candidate.primary_occupation_id is None:
        raise ValueError(
            f"Candidate {candidate_id} has no primary_occupation_id (target occupation) — "
            "course recommendations require a stated target occupation"
        )

    profile = load_occupation_skill_profile(session, candidate.primary_occupation_id)
    gap = analyze_occupation_skill_gap(candidate, candidate.primary_occupation_id, profile)

    coverage = load_course_skill_coverage(session)
    titles = load_course_titles(session)
    recommendations = recommend_courses(gap, coverage, titles, top_k=top_k)

    return gap, recommendations
