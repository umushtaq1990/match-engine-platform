"""Matching engine endpoints (Phase 6) — thin HTTP layer over app/matching/.

No business logic here: every route loads a session, calls the existing
matching orchestrators (rank.py / course_recommendations.py), and translates
the result dataclasses into the wire schemas in schemas.py. All routes
require the X-API-Key header (see deps.require_api_key).
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from app.api.analytics_client import log_recommendations_shown
from app.api.deps import DbSession, RequireApiKey
from app.api.schemas import (
    CourseRecommendationOut,
    CourseRecommendationsResponse,
    MatchOut,
    OccupationSkillGapOut,
    ScoreOut,
    SkillGapOut,
)
from app.matching.course_recommendations import recommend_courses_for_candidate
from app.matching.rank import MatchResult, rank_candidates_for_job, rank_jobs_for_candidate

router = APIRouter(dependencies=[RequireApiKey])


def _to_match_out(result: MatchResult) -> MatchOut:
    return MatchOut(
        entity_id=result.entity_id,
        label=result.label,
        eligible=result.eligible,
        ineligible_reason=result.ineligible_reason,
        score=ScoreOut(
            skill=result.score.skill,
            location=result.score.location,
            salary=result.score.salary,
            occupation=result.score.occupation,
            total=result.score.total,
        ),
        skill_gap=SkillGapOut(
            confirmed_absent_required=sorted(result.skill_gap.confirmed_absent_required),
            not_recorded_required=sorted(result.skill_gap.not_recorded_required),
            confirmed_absent_preferred=sorted(result.skill_gap.confirmed_absent_preferred),
            not_recorded_preferred=sorted(result.skill_gap.not_recorded_preferred),
        ),
        explanation=result.explanation,
    )


@router.get("/candidates/{candidate_id}/job-matches", response_model=list[MatchOut])
def get_job_matches_for_candidate(
    candidate_id: int, top_k: int = 10, include_ineligible: bool = False,
    db: Session = DbSession,
):
    """Use Case A: rank jobs for a candidate."""
    try:
        results = rank_jobs_for_candidate(
            db, candidate_id, top_k=top_k, include_ineligible=include_ineligible
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log_recommendations_shown(
        actor_type="candidate", actor_id=candidate_id,
        entity_type="job", entity_ids=[r.entity_id for r in results],
    )
    return [_to_match_out(r) for r in results]


@router.get("/jobs/{job_id}/candidate-matches", response_model=list[MatchOut])
def get_candidate_matches_for_job(
    job_id: int, top_k: int = 10, include_ineligible: bool = False,
    db: Session = DbSession,
):
    """Use Case B: rank candidates for a job.

    No Employer entity exists yet in this domain model (jobs only have a
    `company_name` string) — job_id stands in for the acting employer's
    identity in the logged event, a documented v1 limitation, not an
    oversight. See docs/behavioral-analytics.md.
    """
    try:
        results = rank_candidates_for_job(db, job_id, top_k=top_k, include_ineligible=include_ineligible)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log_recommendations_shown(
        actor_type="employer", actor_id=job_id,
        entity_type="candidate", entity_ids=[r.entity_id for r in results],
    )
    return [_to_match_out(r) for r in results]


@router.get(
    "/candidates/{candidate_id}/course-recommendations",
    response_model=CourseRecommendationsResponse,
)
def get_course_recommendations_for_candidate(
    candidate_id: int, top_k: int = 5, db: Session = DbSession,
):
    """Use Case E: recommend courses closing a candidate's target-occupation skill gap."""
    try:
        gap, recommendations = recommend_courses_for_candidate(db, candidate_id, top_k=top_k)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    log_recommendations_shown(
        actor_type="candidate", actor_id=candidate_id,
        entity_type="course", entity_ids=[r.course_id for r in recommendations],
    )
    return CourseRecommendationsResponse(
        skill_gap=OccupationSkillGapOut(
            occupation_id=gap.occupation_id,
            present_skill_ids=sorted(gap.present),
            confirmed_absent_skill_ids=sorted(gap.confirmed_absent),
            not_recorded_skill_ids=sorted(gap.not_recorded),
            total_gap_weight=round(gap.total_gap_weight, 4),
        ),
        recommendations=[
            CourseRecommendationOut(
                course_id=r.course_id,
                title=r.title,
                coverage_score=r.coverage_score,
                covered_skill_ids=sorted(r.covered_skill_ids),
            )
            for r in recommendations
        ],
    )
