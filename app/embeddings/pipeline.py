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
    if dimension == "skills":
        return compute_skills_embedding(session, entity_type, entity_id, embedding_model)
    if dimension == "experience":
        text = experience_text(session, entity_type, entity_id)
        return embed(embedding_model, text) if text else None
    if dimension == "overall":
        text = overall_text(session, entity_type, entity_id)
        return embed(embedding_model, text) if text else None
    raise ValueError(f"unknown dimension: {dimension}")
