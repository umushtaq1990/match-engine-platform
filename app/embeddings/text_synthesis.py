"""Synthesizes short text for the Experience/Overall embedding dimensions
from existing structured data — honest (derived from real fields), not
fabricated. No raw CV-text field exists for candidates (same gap Phase 16
already flagged), so the candidate side is always synthesized; the job
side uses `description_raw` directly where real free text already exists.
See docs/embedding-matching.md.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.source_models import Candidate, CandidateSkill, Job, Occupation, Skill

# all-minilm's serving context is a hard 256 tokens (confirmed directly from
# ollama's own rejection: "input (268 tokens) is too large to process") --
# candidates' overall_text is always a short synthesized sentence, so this
# never mattered there, but a job's real `description_raw` (LLM-written,
# multi-paragraph for golden jobs) can run well past it. Truncated to a safe
# character budget rather than the full text -- an embedding only needs the
# semantic gist, not a verbatim reproduction; ~800 chars stays comfortably
# under 256 tokens even for dense text.
MAX_JOB_OVERALL_TEXT_CHARS = 800


def _candidate_occupation_title(session: Session, candidate: Candidate) -> str | None:
    if candidate.primary_occupation_id is None:
        return None
    occupation = session.get(Occupation, candidate.primary_occupation_id)
    return occupation.title if occupation else None


def experience_text(session: Session, entity_type: str, entity_id: int) -> str | None:
    if entity_type == "candidate":
        candidate = session.get(Candidate, entity_id)
        if candidate is None:
            return None
        occupation_title = _candidate_occupation_title(session, candidate)
        if occupation_title:
            return f"{candidate.years_experience} years of experience as a {occupation_title}."
        return f"{candidate.years_experience} years of general professional experience."

    job = session.get(Job, entity_id)
    if job is None:
        return None
    employment = job.employment_type.value.replace("_", " ")
    return f"{job.title} position ({employment})."


def overall_text(session: Session, entity_type: str, entity_id: int) -> str | None:
    if entity_type == "candidate":
        candidate = session.get(Candidate, entity_id)
        if candidate is None:
            return None
        occupation_title = _candidate_occupation_title(session, candidate)
        skill_names = session.execute(
            select(Skill.name)
            .join(CandidateSkill, CandidateSkill.skill_id == Skill.id)
            .where(CandidateSkill.candidate_id == entity_id)
        ).scalars().all()
        role_part = f" targeting a {occupation_title} role" if occupation_title else ""
        skills_part = f" Skills include: {', '.join(sorted(skill_names))}." if skill_names else ""
        return f"Candidate with {candidate.years_experience} years of experience{role_part}.{skills_part}"

    job = session.get(Job, entity_id)
    if job is None:
        return None
    return (job.description_raw or "")[:MAX_JOB_OVERALL_TEXT_CHARS] or None
