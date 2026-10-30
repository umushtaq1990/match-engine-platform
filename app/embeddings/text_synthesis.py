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
    return job.description_raw if job else None
