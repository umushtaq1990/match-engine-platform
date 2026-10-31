"""Matching engine endpoints (Phase 6) — thin HTTP layer over app/matching/.

No business logic here: every route loads a session, calls the existing
matching orchestrators (rank.py / course_recommendations.py), and translates
the result dataclasses into the wire schemas in schemas.py. All routes
require the X-API-Key header (see deps.require_api_key).
"""
from fastapi import APIRouter, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.analytics_client import log_recommendations_shown
from app.api.cache import cache_key, get_cached, set_cached
from app.api.deps import DbSession, RequireApiKey
from app.api.schemas import (
    CourseRecommendationOut,
    CourseRecommendationsResponse,
    MatchOut,
    OccupationSkillGapOut,
    ScoreOut,
    SemanticMatchOut,
    SkillGapOut,
)
from app.embeddings.retrieval import rank_jobs_for_candidate_blended_default, rank_jobs_for_candidate_semantic
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
    candidate_id: int, response: Response, top_k: int = 10, include_ineligible: bool = False,
    mode: str = "blended", db: Session = DbSession,
):
    """Use Case A: rank jobs for a candidate.

    Phase 22: `mode="blended"` (default) re-orders the rule-based eligible
    pool by the Phase 17 embeddings-blended score — Phase 20 measured
    blended NDCG 0.878 vs. rule-based's 0.645 (docs/embedding-matching.md),
    the case this promotion was waiting for. Falls back to pure rule-based
    order when the candidate has no embeddings computed yet. Pass
    `mode=rule_based` to opt back into the original deterministic ordering
    (e.g. for auditing eligibility reasoning without an ML dependency).
    The actual ranking path used is reported via `X-Ranking-Mode` —
    "blended", "rule_based", or "rule_based_no_embeddings" (fallback) — the
    same transparency discipline as `X-Cache`.

    Phase 21: cache-aside, 1-hour TTL (docs/redis-infrastructure.md "Use case
    2") — an `X-Cache: HIT`/`MISS` header makes the behavior demonstrable.
    A cached hit still logs a recommendation_shown impression: the user is
    seeing the recommendation again regardless of whether it was recomputed.
    """
    key = cache_key("job-matches", candidate_id, top_k, include_ineligible, mode)
    cached = get_cached(key)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        response.headers["X-Ranking-Mode"] = cached["ranking_mode"]
        log_recommendations_shown(
            actor_type="candidate", actor_id=candidate_id,
            entity_type="job", entity_ids=[m["entity_id"] for m in cached["matches"]],
        )
        return cached["matches"]

    try:
        if mode == "rule_based":
            results, ranking_mode = rank_jobs_for_candidate(
                db, candidate_id, top_k=top_k, include_ineligible=include_ineligible
            ), "rule_based"
        else:
            results, ranking_mode = rank_jobs_for_candidate_blended_default(
                db, candidate_id, top_k=top_k, include_ineligible=include_ineligible
            )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log_recommendations_shown(
        actor_type="candidate", actor_id=candidate_id,
        entity_type="job", entity_ids=[r.entity_id for r in results],
    )
    matches = [_to_match_out(r) for r in results]
    response.headers["X-Cache"] = "MISS"
    response.headers["X-Ranking-Mode"] = ranking_mode
    set_cached(key, {"matches": [m.model_dump() for m in matches], "ranking_mode": ranking_mode})
    return matches


@router.get("/candidates/{candidate_id}/job-matches/semantic", response_model=list[SemanticMatchOut])
def get_job_matches_for_candidate_semantic(
    candidate_id: int, response: Response, top_k: int = 10, db: Session = DbSession,
):
    """Phase 17: the raw embeddings-blended score breakdown (skills/
    experience/overall similarity, no eligibility/skill-gap fields) — the
    same blended score Use Case A's default `mode=blended` above now uses
    to re-rank, exposed here on its own for inspection (see
    docs/embedding-matching.md). Requires embeddings to already be computed
    for this candidate (`python -m app.embeddings.compute_embeddings`) —
    this route never calls the embedding model live.

    Phase 21: same cache-aside, 1-hour TTL policy as the rule-based endpoint
    above (docs/redis-infrastructure.md "Use case 2").
    """
    key = cache_key("job-matches-semantic", candidate_id, top_k)
    cached = get_cached(key)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return cached

    try:
        results = rank_jobs_for_candidate_semantic(db, candidate_id, top_k=top_k)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    matches = [
        SemanticMatchOut(
            job_id=r.job_id, total_score=r.total_score,
            skills_similarity=r.skills_similarity, experience_similarity=r.experience_similarity,
            overall_similarity=r.overall_similarity, location_score=r.location_score,
            salary_score=r.salary_score, occupation_score=r.occupation_score,
        )
        for r in results
    ]
    response.headers["X-Cache"] = "MISS"
    set_cached(key, [m.model_dump() for m in matches])
    return matches


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
