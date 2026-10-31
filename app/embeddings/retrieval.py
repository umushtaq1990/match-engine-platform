"""Two-stage retrieve-then-score for embeddings-based job matching (Phase
17): stage 1 is a pgvector ANN query (cheap, approximate) that narrows the
full job population to a plausible top-N; stage 2 computes the full blended
score (skills/experience/overall similarity + the existing rule-based
location/salary/occupation components) only on that narrowed set. See
docs/embedding-matching.md.

No live Ollama calls on this path — embeddings are precomputed offline
(compute_embeddings.py); retrieval here is pure Postgres/pgvector at
request time, which is the entire point of precomputing them.
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.models import EntityEmbedding
from app.matching.profiles import load_candidate_profile, load_job_profiles_by_ids
from app.matching.rank import MatchResult, rank_jobs_for_candidate
from app.matching.scoring import _location_component, _occupation_component, _salary_component

DEFAULT_EMBEDDING_MODEL = "all-minilm"
STAGE1_TOP_N = 200

# Provisional, documented weights (same "not presented as objectively
# correct" discipline as app/matching/scoring.py's own WEIGHTS) for the
# blended semantic score. Replaces that module's single "skill" weight with
# three embedding dimensions; location/salary/occupation stay identical to
# the rule-based score — embeddings add nothing there.
BLENDED_WEIGHTS = {
    "skills": 0.35,
    "experience": 0.15,
    "overall": 0.15,
    "location": 0.15,
    "salary": 0.10,
    "occupation": 0.10,
}
# Same three embedding dimensions, renormalized to sum to 1.0 with the
# rule-based components excluded entirely — used by the evaluation harness
# (app/embeddings/run_embedding_eval.py) to measure what embeddings alone
# contribute, separate from "blended", which already includes them.
EMBEDDINGS_ONLY_WEIGHTS = {
    "skills": 0.35 / 0.65,
    "experience": 0.15 / 0.65,
    "overall": 0.15 / 0.65,
    "location": 0.0,
    "salary": 0.0,
    "occupation": 0.0,
}
# Neutral score for a dimension with no embedding yet — consistent with the
# project's existing "not recorded != failing" rule (see
# app/matching/scoring.py's UNKNOWN_DATA_SCORE).
UNKNOWN_SIMILARITY_SCORE = 0.5


@dataclass
class SemanticMatchResult:
    job_id: int
    total_score: float
    skills_similarity: float | None
    experience_similarity: float | None
    overall_similarity: float | None
    location_score: float
    salary_score: float
    occupation_score: float


def _get_vector(
    session: Session, entity_type: str, entity_id: int, dimension: str, embedding_model: str
) -> list[float] | None:
    row = session.execute(
        select(EntityEmbedding.vector).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id == entity_id,
            EntityEmbedding.dimension == dimension,
            EntityEmbedding.embedding_model == embedding_model,
        )
    ).scalar_one_or_none()
    return list(row) if row is not None else None


def _get_vectors_batch(
    session: Session, entity_type: str, entity_ids: list[int], embedding_model: str
) -> dict[int, dict[str, list[float]]]:
    """One query for every (entity_id, dimension) vector needed, instead of
    one `_get_vector` call per entity per dimension — fixes a real N+1 found
    by Phase 20's stress test (up to ~5 queries per job in stage 2,
    50x-amplified at the 50,000-job target scale). See docs/data-at-scale.md.
    """
    if not entity_ids:
        return {}
    rows = session.execute(
        select(EntityEmbedding.entity_id, EntityEmbedding.dimension, EntityEmbedding.vector).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id.in_(entity_ids),
            EntityEmbedding.embedding_model == embedding_model,
        )
    ).all()
    result: dict[int, dict[str, list[float]]] = {}
    for entity_id, dimension, vector in rows:
        result.setdefault(entity_id, {})[dimension] = list(vector)
    return result


def _stage1_job_ids(
    session: Session, candidate_vector: list[float], embedding_model: str, top_n: int
) -> list[int]:
    stmt = (
        select(EntityEmbedding.entity_id)
        .where(
            EntityEmbedding.entity_type == "job",
            EntityEmbedding.dimension == "skills",
            EntityEmbedding.embedding_model == embedding_model,
        )
        .order_by(EntityEmbedding.vector.cosine_distance(candidate_vector))
        .limit(top_n)
    )
    return list(session.execute(stmt).scalars().all())


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def rank_jobs_for_candidate_semantic(
    session: Session,
    candidate_id: int,
    top_k: int = 10,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    stage1_top_n: int = STAGE1_TOP_N,
    mode: str = "blended",
) -> list[SemanticMatchResult]:
    """`mode`: "blended" (default — embeddings + existing rule-based
    location/salary/occupation components) or "embeddings_only" (the three
    embedding dimensions alone, rule-based components excluded) — used by
    the evaluation harness to isolate what embeddings contribute on their
    own, separate from the blend. See docs/embedding-matching.md.
    """
    weights = BLENDED_WEIGHTS if mode == "blended" else EMBEDDINGS_ONLY_WEIGHTS
    candidate_profile = load_candidate_profile(session, candidate_id)
    if candidate_profile is None:
        raise ValueError(f"Candidate {candidate_id} not found")

    candidate_skills_vector = _get_vector(session, "candidate", candidate_id, "skills", embedding_model)
    if candidate_skills_vector is None:
        raise ValueError(
            f"No skills embedding for candidate {candidate_id} with model "
            f"{embedding_model!r} — run `python -m app.embeddings.compute_embeddings` first"
        )
    candidate_experience_vector = _get_vector(
        session, "candidate", candidate_id, "experience", embedding_model
    )
    candidate_overall_vector = _get_vector(session, "candidate", candidate_id, "overall", embedding_model)

    job_ids = _stage1_job_ids(session, candidate_skills_vector, embedding_model, stage1_top_n)

    # Both batch-loaded once for the whole stage-1 candidate set, not once
    # per job_id — see _get_vectors_batch's docstring for the N+1 this fixes.
    job_profiles = load_job_profiles_by_ids(session, job_ids)
    job_vectors = _get_vectors_batch(session, "job", job_ids, embedding_model)

    results = []
    for job_id in job_ids:
        job_profile = job_profiles.get(job_id)
        if job_profile is None:
            continue

        vectors = job_vectors.get(job_id, {})
        job_skills_vector = vectors.get("skills")
        job_experience_vector = vectors.get("experience")
        job_overall_vector = vectors.get("overall")

        skills_sim = (
            _cosine_similarity(candidate_skills_vector, job_skills_vector)
            if job_skills_vector is not None else 0.0
        )
        experience_sim = (
            _cosine_similarity(candidate_experience_vector, job_experience_vector)
            if candidate_experience_vector is not None and job_experience_vector is not None
            else None
        )
        overall_sim = (
            _cosine_similarity(candidate_overall_vector, job_overall_vector)
            if candidate_overall_vector is not None and job_overall_vector is not None
            else None
        )

        location_score = _location_component(candidate_profile, job_profile)
        salary_score = _salary_component(candidate_profile, job_profile)
        occupation_score = _occupation_component(candidate_profile, job_profile)

        total = (
            weights["skills"] * skills_sim
            + weights["experience"] * (
                experience_sim if experience_sim is not None else UNKNOWN_SIMILARITY_SCORE
            )
            + weights["overall"] * (
                overall_sim if overall_sim is not None else UNKNOWN_SIMILARITY_SCORE
            )
            + weights["location"] * location_score
            + weights["salary"] * salary_score
            + weights["occupation"] * occupation_score
        )

        results.append(SemanticMatchResult(
            job_id=job_id,
            total_score=round(total, 4),
            skills_similarity=round(skills_sim, 4),
            experience_similarity=round(experience_sim, 4) if experience_sim is not None else None,
            overall_similarity=round(overall_sim, 4) if overall_sim is not None else None,
            location_score=round(location_score, 4),
            salary_score=round(salary_score, 4),
            occupation_score=round(occupation_score, 4),
        ))

    results.sort(key=lambda r: r.total_score, reverse=True)
    return results[:top_k]


def rank_jobs_for_candidate_blended_default(
    session: Session, candidate_id: int, top_k: int = 10, include_ineligible: bool = False,
) -> tuple[list[MatchResult], str]:
    """Phase 22: promotes blended ranking to the default path for Use Case A
    — Phase 20's re-verification measured blended NDCG 0.878 vs. rule-
    based's 0.645 (docs/embedding-matching.md), a strong enough, stable
    enough result to stop treating rule-based as the permanent default.

    Keeps every rule-based field (eligibility, skill_gap, explanation) —
    embeddings don't produce those — and only uses the embedding-based
    blended score to *re-order* the already-eligibility-filtered rule-based
    result set. Falls back to pure rule-based order, unchanged, when the
    candidate has no embeddings yet (embeddings-compute is a batch job, not
    guaranteed to have run for every candidate) rather than erroring —
    returns which path was actually used so the caller can report it
    (app/api/routes.py's `X-Ranking-Mode` header), the same transparency
    discipline as `X-Cache`.
    """
    rule_based_results = rank_jobs_for_candidate(
        session, candidate_id, top_k=None, include_ineligible=include_ineligible
    )
    if not rule_based_results:
        return rule_based_results[:top_k], "rule_based"

    try:
        semantic_results = rank_jobs_for_candidate_semantic(
            session, candidate_id, top_k=len(rule_based_results),
        )
    except ValueError:
        return rule_based_results[:top_k], "rule_based_no_embeddings"

    blended_score_by_job = {r.job_id: r.total_score for r in semantic_results}
    # Jobs outside the stage-1 ANN retrieval window (no blended score) sort
    # last, in their original rule-based order — Python's sort is stable, so
    # ties at the "no blended score" floor preserve the prior ordering.
    ranked = sorted(
        rule_based_results,
        key=lambda r: blended_score_by_job.get(r.entity_id, float("-inf")),
        reverse=True,
    )
    return ranked[:top_k], "blended"
