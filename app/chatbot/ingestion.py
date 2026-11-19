"""Builds app.chatbot.models.DocumentChunk rows from existing domain data --
the knowledge base's actual content. Dispatch-by-source_type (one builder
function per type, registered in CHUNK_BUILDERS below) mirrors
app.embeddings.text_synthesis's builder-registry pattern: adding a new
source type is a new function + one dict entry, not a new if/elif branch.

Job postings have real free text (Job.description_raw) and are chunked
directly. Candidates have NO raw CV text anywhere in this schema (same gap
app.embeddings.text_synthesis's own docstring flags) -- only CVs-as-PDF in
object storage, not cached as text -- so candidate chunks are honestly
synthesized from structured fields, same "derived from real fields, not
fabricated" discipline text_synthesis.py already follows. Skill/course
chunks are short templated sentences from structured fields.

Idempotent via content_hash (same freshness discipline as
app.embeddings.models.EntityEmbedding.source_hash / app.golden.models.
GraphNarrative.facts_hash): re-running ingestion only touches a chunk
whose underlying source data actually changed.
"""
import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chatbot.models import VISIBILITY_CONFIDENTIAL, VISIBILITY_PUBLIC, DocumentChunk
from app.ingestion.source_models import (
    Candidate,
    CandidateSkill,
    Course,
    CourseSkill,
    Job,
    Occupation,
    Skill,
)

SOURCE_JOB_DESCRIPTION = "job_description"
SOURCE_CANDIDATE_PROFILE = "candidate_profile"
SOURCE_SKILL = "skill"
SOURCE_COURSE = "course"

# all-minilm's serving context is a hard 256 tokens (confirmed directly from
# Ollama's own rejection -- same constraint app.embeddings.text_synthesis's
# MAX_JOB_OVERALL_TEXT_CHARS already documents, ~3 chars/token conservatively).
# Caught live here too: a real job description exceeded it and crashed a
# full-population embedding run with a 500 from /api/embeddings. Unlike
# text_synthesis.py (which truncates, acceptable there since it only feeds
# one embedding dimension of several), a real chunking pipeline should
# split long text into multiple chunks instead of discarding the rest --
# more granular retrieval is a feature here, not just a workaround.
_MAX_CHUNK_CHARS = 700


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_into_chunks(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def _build_job_chunks(session: Session, job_id: int) -> list[dict]:
    """One or more chunks per job: title + company + the real description
    text, split into `_MAX_CHUNK_CHARS`-sized pieces if long (public --
    job postings are, by nature, meant to be seen broadly).
    """
    job = session.get(Job, job_id)
    if job is None or not job.description_raw:
        return []
    text = f"Job posting: {job.title} at {job.company_name}.\n\n{job.description_raw}"
    return [
        {"text": piece, "tags": ["job"], "visibility": VISIBILITY_PUBLIC}
        for piece in _split_into_chunks(text)
    ]


def _build_candidate_chunks(session: Session, candidate_id: int) -> list[dict]:
    """One synthesized chunk per candidate from structured facts --
    years of experience, target occupation, degree, and skills with
    proficiency. Confidential: candidate data is personal, unlike a job
    posting or a skill/course definition.
    """
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        return []

    occupation_title = None
    if candidate.primary_occupation_id is not None:
        occupation = session.get(Occupation, candidate.primary_occupation_id)
        occupation_title = occupation.title if occupation else None

    skill_rows = session.execute(
        select(Skill.name, CandidateSkill.proficiency)
        .join(CandidateSkill, CandidateSkill.skill_id == Skill.id)
        .where(CandidateSkill.candidate_id == candidate_id)
        .order_by(Skill.name)
    ).all()
    skills_part = (
        "Skills: " + ", ".join(f"{name} ({proficiency.value})" for name, proficiency in skill_rows) + "."
        if skill_rows else ""
    )
    role_part = f" Targeting a {occupation_title} role." if occupation_title else ""
    degree_part = ""
    if candidate.highest_degree_level is not None:
        degree_part = f" Highest education: {candidate.highest_degree_level.value}"
        degree_part += f" in {candidate.field_of_study}." if candidate.field_of_study else "."

    text = (
        f"Candidate: {candidate.full_name} (#{candidate.id}). "
        f"{candidate.years_experience} years of experience.{role_part}{degree_part} {skills_part}"
    ).strip()
    return [
        {"text": piece, "tags": ["candidate"], "visibility": VISIBILITY_CONFIDENTIAL}
        for piece in _split_into_chunks(text)
    ]


def _build_skill_chunks(session: Session, skill_id: int) -> list[dict]:
    """One templated sentence per skill -- public, same category as job
    postings/course catalogs: reference-taxonomy content, not personal
    data.
    """
    skill = session.get(Skill, skill_id)
    if skill is None:
        return []
    category_part = f" Category: {skill.category}." if skill.category else ""
    text = f"Skill: {skill.name}.{category_part}"
    return [{"text": text, "tags": ["skill"], "visibility": VISIBILITY_PUBLIC}]


def _build_course_chunks(session: Session, course_id: int) -> list[dict]:
    course = session.get(Course, course_id)
    if course is None:
        return []
    skill_names = session.execute(
        select(Skill.name)
        .join(CourseSkill, CourseSkill.skill_id == Skill.id)
        .where(CourseSkill.course_id == course_id)
    ).scalars().all()
    skills_part = f" Teaches: {', '.join(sorted(skill_names))}." if skill_names else ""
    text = (
        f"Course: {course.title}, provided by {course.provider} "
        f"({course.level} level, {course.duration_hours} hours).{skills_part}"
    )
    return [
        {"text": piece, "tags": ["course"], "visibility": VISIBILITY_PUBLIC}
        for piece in _split_into_chunks(text)
    ]


CHUNK_BUILDERS = {
    SOURCE_JOB_DESCRIPTION: _build_job_chunks,
    SOURCE_CANDIDATE_PROFILE: _build_candidate_chunks,
    SOURCE_SKILL: _build_skill_chunks,
    SOURCE_COURSE: _build_course_chunks,
}


def ingest_entity(session: Session, source_type: str, source_entity_id: int) -> int:
    """Builds/refreshes one entity's `DocumentChunk` row(s). Idempotent: a
    chunk whose `content_hash` already matches the freshly-built text is
    left untouched; existing chunks are updated in place rather than
    duplicated.

    Args:
        session: an active SQLAlchemy session; callers commit.
        source_type: one of `CHUNK_BUILDERS`'s keys.
        source_entity_id: the domain row's primary key.

    Returns:
        int: how many chunks were created or updated (0 if the source
            entity has nothing to chunk, or didn't exist).

    Raises:
        ValueError: if `source_type` has no registered builder.
    """
    try:
        builder = CHUNK_BUILDERS[source_type]
    except KeyError:
        raise ValueError(f"No chunk builder registered for source_type={source_type!r}") from None

    built = builder(session, source_entity_id)
    now = datetime.now(timezone.utc)
    touched = 0
    for chunk_index, spec in enumerate(built):
        content_hash = _hash(spec["text"])
        existing = session.execute(
            select(DocumentChunk).where(
                DocumentChunk.source_type == source_type,
                DocumentChunk.source_entity_id == source_entity_id,
                DocumentChunk.chunk_index == chunk_index,
            )
        ).scalar_one_or_none()

        if existing is not None and existing.content_hash == content_hash:
            continue

        if existing is not None:
            existing.text = spec["text"]
            existing.tags = spec["tags"]
            existing.visibility = spec["visibility"]
            existing.content_hash = content_hash
            existing.updated_at = now
        else:
            session.add(DocumentChunk(
                source_type=source_type, source_entity_id=source_entity_id, chunk_index=chunk_index,
                text=spec["text"], tags=spec["tags"], visibility=spec["visibility"],
                content_hash=content_hash, updated_at=now,
            ))
        touched += 1
    return touched


def ingest_all(
    session: Session, job_limit: int | None = None, candidate_limit: int | None = None,
) -> dict[str, int]:
    """Walks every registered source type and ingests every entity of that
    type (skills/courses in full; jobs/candidates capped by the limit args
    for a fast sample pass, same `--candidate-limit`/`--job-limit` spirit
    as `app.embeddings.compute_embeddings.refresh_all`).

    Returns:
        dict[str, int]: chunks touched (created or updated) per source_type.
    """
    totals = {source_type: 0 for source_type in CHUNK_BUILDERS}

    job_ids = session.execute(select(Job.id).order_by(Job.id).limit(job_limit)).scalars().all()
    for job_id in job_ids:
        totals[SOURCE_JOB_DESCRIPTION] += ingest_entity(session, SOURCE_JOB_DESCRIPTION, job_id)

    candidate_ids = session.execute(
        select(Candidate.id).order_by(Candidate.id).limit(candidate_limit)
    ).scalars().all()
    for candidate_id in candidate_ids:
        totals[SOURCE_CANDIDATE_PROFILE] += ingest_entity(session, SOURCE_CANDIDATE_PROFILE, candidate_id)

    for skill_id in session.execute(select(Skill.id)).scalars().all():
        totals[SOURCE_SKILL] += ingest_entity(session, SOURCE_SKILL, skill_id)

    for course_id in session.execute(select(Course.id)).scalars().all():
        totals[SOURCE_COURSE] += ingest_entity(session, SOURCE_COURSE, course_id)

    session.commit()
    return totals
