"""Source-data hashing for embedding invalidation (Phase 17). A change in
the underlying data changes the hash, marking the stored vector stale —
see docs/embedding-matching.md "Invalidation". Hashing the synthesized text
itself (for Experience/Overall) rather than re-deriving from raw fields
separately is deliberate: it also catches template changes, which should
just as validly trigger a recompute.

Both dimension dispatch (`DIMENSION_HASHERS`) and, within the Skills
dimension, entity-type dispatch (`_SKILLS_SOURCE_HASHERS`) are plain dict
registries rather than `if`/`elif` chains — adding a dimension or entity
type elsewhere is a dict entry, not an edit to this module's internals.
"""
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.text_synthesis import experience_text, overall_text
from app.ingestion.source_models import CandidateSkill, JobSkill


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate_skills_source_hash(session: Session, entity_id: int) -> str | None:
    rows = session.execute(
        select(CandidateSkill.skill_id, CandidateSkill.proficiency).where(
            CandidateSkill.candidate_id == entity_id
        )
    ).all()
    parts = sorted((skill_id, proficiency.value) for skill_id, proficiency in rows)
    if not parts:
        return None
    return _hash(repr(parts))


def _job_skills_source_hash(session: Session, entity_id: int) -> str | None:
    rows = session.execute(
        select(JobSkill.skill_id, JobSkill.requirement_level).where(JobSkill.job_id == entity_id)
    ).all()
    parts = sorted((skill_id, level.value) for skill_id, level in rows)
    if not parts:
        return None
    return _hash(repr(parts))


_SKILLS_SOURCE_HASHERS = {
    "candidate": _candidate_skills_source_hash,
    "job": _job_skills_source_hash,
}


def skills_source_hash(session: Session, entity_type: str, entity_id: int) -> str | None:
    """Hashes the Skills-dimension source data for one entity (its current
    skill set, each with the proficiency/requirement weight that would
    affect the computed vector).

    Args:
        session: an active SQLAlchemy session.
        entity_type: `"candidate"` or `"job"`.
        entity_id: the candidate's or job's primary key.

    Returns:
        str | None: a stable hex digest, or `None` if the entity has no
            skill rows at all (nothing to hash -- distinct from "unchanged
            since last hash", which still returns a real digest).

    Raises:
        ValueError: if `entity_type` has no registered hasher.
    """
    try:
        hasher = _SKILLS_SOURCE_HASHERS[entity_type]
    except KeyError:
        raise ValueError(f"No skills_source_hash hasher registered for entity_type={entity_type!r}") from None
    return hasher(session, entity_id)


def experience_source_hash(session: Session, entity_type: str, entity_id: int) -> str | None:
    """Hashes the synthesized Experience-dimension text (see
    `app.embeddings.text_synthesis.experience_text`) for one entity.

    Args:
        session: an active SQLAlchemy session.
        entity_type: `"candidate"` or `"job"`.
        entity_id: the candidate's or job's primary key.

    Returns:
        str | None: a stable hex digest, or `None` if no experience text
            could be built (e.g. the entity doesn't exist).
    """
    text = experience_text(session, entity_type, entity_id)
    return _hash(text) if text is not None else None


def overall_source_hash(session: Session, entity_type: str, entity_id: int) -> str | None:
    """Hashes the synthesized Overall-dimension text (see
    `app.embeddings.text_synthesis.overall_text`) for one entity.

    Args:
        session: an active SQLAlchemy session.
        entity_type: `"candidate"` or `"job"`.
        entity_id: the candidate's or job's primary key.

    Returns:
        str | None: a stable hex digest, or `None` if no overall text
            could be built.
    """
    text = overall_text(session, entity_type, entity_id)
    return _hash(text) if text is not None else None


DIMENSION_HASHERS = {
    "skills": skills_source_hash,
    "experience": experience_source_hash,
    "overall": overall_source_hash,
}
"""Maps each embedding dimension to the hasher that computes its source
hash. `app.embeddings.compute_embeddings.refresh_entity` iterates this
dict's keys as its list of dimensions to refresh -- adding a dimension
here is enough to make it part of a refresh run, no other file needs an
edit for the hashing side of that addition."""
