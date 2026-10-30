"""Caches embeddings of the canonical skill taxonomy (names + known
synonyms) so they're computed once per process, not once per extraction
call — there are only ~70 reference texts (64 skills + synonyms), small and
static, a legitimate in-memory cache rather than premature optimization.
See docs/skill-extraction.md.
"""
from sqlalchemy.orm import Session

from app.extraction.ollama_client import embed
from app.ingestion.source_models import Skill, SkillSynonym

# Keyed by embedding_model — a different model means different vectors, so a
# single process that evaluates multiple embedding models keeps both cached.
_cache: dict[str, list[tuple[int, str, list[float]]]] = {}


def get_reference_embeddings(session: Session, embedding_model: str) -> list[tuple[int, str, list[float]]]:
    if embedding_model in _cache:
        return _cache[embedding_model]

    reference_texts: list[tuple[int, str]] = [
        (skill.id, skill.name) for skill in session.query(Skill).all()
    ]
    reference_texts += [
        (syn.skill_id, syn.synonym_text) for syn in session.query(SkillSynonym).all()
    ]

    reference = [
        (skill_id, text, embed(embedding_model, text)) for skill_id, text in reference_texts
    ]
    _cache[embedding_model] = reference
    return reference


def clear_cache() -> None:
    _cache.clear()
