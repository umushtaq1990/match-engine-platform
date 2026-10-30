"""Source-data hashing for embedding invalidation (Phase 17). A change in
the underlying data changes the hash, marking the stored vector stale —
see docs/embedding-matching.md "Invalidation". Hashing the synthesized text
itself (for Experience/Overall) rather than re-deriving from raw fields
separately is deliberate: it also catches template changes, which should
just as validly trigger a recompute.
"""
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.text_synthesis import experience_text, overall_text
from app.ingestion.source_models import CandidateSkill, JobSkill


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def skills_source_hash(session: Session, entity_type: str, entity_id: int) -> str | None:
    if entity_type == "candidate":
        rows = session.execute(
            select(CandidateSkill.skill_id, CandidateSkill.proficiency).where(
                CandidateSkill.candidate_id == entity_id
            )
        ).all()
        parts = sorted((skill_id, proficiency.value) for skill_id, proficiency in rows)
    else:
        rows = session.execute(
            select(JobSkill.skill_id, JobSkill.requirement_level).where(JobSkill.job_id == entity_id)
        ).all()
        parts = sorted((skill_id, level.value) for skill_id, level in rows)

    if not parts:
        return None
    return _hash(repr(parts))


def experience_source_hash(session: Session, entity_type: str, entity_id: int) -> str | None:
    text = experience_text(session, entity_type, entity_id)
    return _hash(text) if text is not None else None


def overall_source_hash(session: Session, entity_type: str, entity_id: int) -> str | None:
    text = overall_text(session, entity_type, entity_id)
    return _hash(text) if text is not None else None


DIMENSION_HASHERS = {
    "skills": skills_source_hash,
    "experience": experience_source_hash,
    "overall": overall_source_hash,
}
