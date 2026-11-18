"""Computes entity embeddings for the Skills/Experience/Overall dimensions
(Phase 17). Skills reuses Phase 16's cached per-skill embeddings directly —
no new LLM call, pure vector math. Experience/Overall synthesize short text
(app/embeddings/text_synthesis.py) and call the embedding model once each.
See docs/embedding-matching.md.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.text_synthesis import experience_text, overall_text
from app.extraction.ollama_client import embed
from app.extraction.skill_cache import get_reference_embeddings
from app.ingestion.source_models import CandidateSkill, JobSkill, ProficiencyLevel, RequirementLevel

# Provisional, documented weights (same "not presented as objectively
# correct" discipline as app/matching/scoring.py's own weights) for
# averaging per-skill vectors into one Skills-dimension embedding.
PROFICIENCY_WEIGHTS = {
    ProficiencyLevel.beginner: 0.25,
    ProficiencyLevel.intermediate: 0.5,
    ProficiencyLevel.advanced: 0.75,
    ProficiencyLevel.expert: 1.0,
}
REQUIREMENT_WEIGHTS = {
    RequirementLevel.required: 1.0,
    RequirementLevel.preferred: 0.6,
}


def _canonical_skill_vectors(session: Session, embedding_model: str) -> dict[int, list[float]]:
    """app/extraction/skill_cache.py's reference set has one row per
    canonical name AND per synonym, in that order (canonical name inserted
    first per skill_id — see its construction loop), so the first occurrence
    per skill_id here is reliably the canonical-name vector, not a synonym's."""
    reference = get_reference_embeddings(session, embedding_model)
    canonical: dict[int, list[float]] = {}
    for skill_id, _text, vector in reference:
        canonical.setdefault(skill_id, vector)
    return canonical


def _weighted_average(pairs: list[tuple[list[float], float]]) -> list[float] | None:
    """Averages a list of vectors, each with its own weight. The one
    genuinely domain-free function in this module — no entity-type
    knowledge, no skill/job concepts, just vector math — reusable as-is
    anywhere a weighted combination of fixed-width vectors is needed.

    Args:
        pairs: `(vector, weight)` tuples; every vector must have the same
            length. Weights need not sum to 1.0 — normalized internally.

    Returns:
        list[float] | None: the weighted-average vector, or `None` if
            `pairs` is empty or every weight is zero (nothing to average).
    """
    if not pairs:
        return None
    total_weight = sum(weight for _, weight in pairs)
    if total_weight == 0:
        return None
    dim = len(pairs[0][0])
    result = [0.0] * dim
    for vector, weight in pairs:
        for i, value in enumerate(vector):
            result[i] += value * weight
    return [value / total_weight for value in result]


def compute_skills_embedding(
    session: Session, entity_type: str, entity_id: int, embedding_model: str
) -> list[float] | None:
    """Computes the Skills-dimension embedding: a weighted average of the
    entity's skills' canonical vectors, no new LLM call (reuses Phase 16's
    cached per-skill embeddings via `get_reference_embeddings`).

    Args:
        session: an active SQLAlchemy session.
        entity_type: `"candidate"` (weighted by `PROFICIENCY_WEIGHTS`) or
            `"job"` (weighted by `REQUIREMENT_WEIGHTS`).
        entity_id: the candidate's or job's primary key.
        embedding_model: which cached skill-embedding set to use.

    Returns:
        list[float] | None: the weighted-average vector, or `None` if the
            entity has no skills with a cached vector.
    """
    canonical_vectors = _canonical_skill_vectors(session, embedding_model)

    if entity_type == "candidate":
        rows = session.execute(
            select(CandidateSkill.skill_id, CandidateSkill.proficiency).where(
                CandidateSkill.candidate_id == entity_id
            )
        ).all()
        weighted = [
            (canonical_vectors[skill_id], PROFICIENCY_WEIGHTS[proficiency])
            for skill_id, proficiency in rows if skill_id in canonical_vectors
        ]
    else:
        rows = session.execute(
            select(JobSkill.skill_id, JobSkill.requirement_level).where(JobSkill.job_id == entity_id)
        ).all()
        weighted = [
            (canonical_vectors[skill_id], REQUIREMENT_WEIGHTS[level])
            for skill_id, level in rows if skill_id in canonical_vectors
        ]

    return _weighted_average(weighted)


def compute_embedding(
    session: Session, entity_type: str, entity_id: int, dimension: str, embedding_model: str
) -> list[float] | None:
    """Computes one entity's embedding for one dimension — the single
    entry point `app.embeddings.compute_embeddings.refresh_entity` calls
    for every `(dimension, entity)` pair that needs a (re)compute.

    Args:
        session: an active SQLAlchemy session.
        entity_type: `"candidate"` or `"job"`.
        entity_id: the candidate's or job's primary key.
        dimension: `"skills"` (pure vector math, no LLM call), or
            `"experience"`/`"overall"` (synthesizes text via
            `app.embeddings.text_synthesis`, then one `embed()` call).
        embedding_model: which model to use for text-based dimensions
            (and which cached skill-vector set to use for `"skills"`).

    Returns:
        list[float] | None: the computed vector, or `None` if there's no
            source data for this dimension (e.g. no skills, or no
            synthesizable text).

    Raises:
        ValueError: if `dimension` isn't one of the three known values.
    """
    if dimension == "skills":
        return compute_skills_embedding(session, entity_type, entity_id, embedding_model)
    if dimension == "experience":
        text = experience_text(session, entity_type, entity_id)
        return embed(embedding_model, text) if text else None
    if dimension == "overall":
        text = overall_text(session, entity_type, entity_id)
        return embed(embedding_model, text) if text else None
    raise ValueError(f"unknown dimension: {dimension}")
