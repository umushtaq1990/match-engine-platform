"""The two tools the chat agent (app/chatbot/graph.py) can call. Both query
the domain tables directly (`app.ingestion.source_models`), not
`app.golden.drilldown.data`'s loaders -- that module is this specific
Streamlit app's presentation layer; importing it here would tie this
otherwise-portable package to one UI, defeating the point of a decoupled
package a little duplication of a short query is the right tradeoff.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chatbot.access_control import allowed_visibilities
from app.chatbot.retrieval import RetrievedChunk, retrieve_chunks
from app.ingestion.source_models import (
    Candidate,
    CandidateSkill,
    Course,
    CourseSkill,
    Job,
    JobSkill,
    Occupation,
    Skill,
)

TOOL_SEARCH_KNOWLEDGE_BASE = "search_knowledge_base"
TOOL_LOOKUP_STRUCTURED_DATA = "lookup_structured_data"

TOOL_SPECS = [
    {
        "name": TOOL_SEARCH_KNOWLEDGE_BASE,
        "description": (
            "Semantic search over the knowledge base (job postings, candidate profiles, skills, "
            "courses). Use for open-ended or descriptive questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "what to search for"}},
            "required": ["query"],
        },
    },
    {
        "name": TOOL_LOOKUP_STRUCTURED_DATA,
        "description": (
            "Exact structured lookup for one specific candidate, job, skill, or course -- years of "
            "experience, occupation, exact skill list, skill category, or course provider/taught-skills. "
            "Use when the question names a specific id (candidate/job) or a specific name (skill/course) "
            "and needs a precise value, not a semantic search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "enum": ["candidate", "job", "skill", "course"]},
                "entity_id": {"type": "integer", "description": "for candidate/job, named by id in the question"},
                "entity_name": {
                    "type": "string",
                    "description": "for skill/course, named by name/title in the question",
                },
            },
            "required": ["entity_type"],
        },
    },
]


def search_knowledge_base(session: Session, role: str, query: str, top_k: int = 5) -> list[RetrievedChunk]:
    """The `search_knowledge_base` tool's real implementation --
    `app.chatbot.retrieval.retrieve_chunks`, unmodified. A thin pass-
    through, kept as its own function so the tool-calling surface
    (`TOOL_SPECS`) and the actual retrieval logic stay independently
    testable.
    """
    return retrieve_chunks(session, query, role, top_k=top_k)


def lookup_structured_data(
    session: Session, role: str, entity_type: str,
    entity_id: int | None = None, entity_name: str | None = None,
) -> dict:
    """The `lookup_structured_data` tool's real implementation. Returns an
    empty dict (not raises) for an entity `role` can't see or that doesn't
    exist -- fails closed, consistent with
    `app.chatbot.access_control.allowed_visibilities`'s own default.

    Candidate/job are looked up by `entity_id` -- a question naturally
    names them by id ("candidate 42"), there is no other stable
    identifier. Skill/course are looked up by `entity_name` instead (an
    id is also accepted, e.g. from the drilldown UI, which already knows
    one) -- a question about a skill or course naturally names it by
    name/title, never by its internal primary key, unlike candidate/job.

    Args:
        session: an active SQLAlchemy session.
        role: gates candidate data the same way chunk retrieval does --
            only a role allowed to see `"confidential"` content gets a
            candidate's structured facts.
        entity_type: `"candidate"`, `"job"`, `"skill"`, or `"course"`.
        entity_id: the primary key, when known.
        entity_name: the skill's exact name or course's exact title, when
            the id isn't known (the common case for a chat question).

    Returns:
        dict: entity-specific structured facts; `{}` if not found/not
            permitted; for a course title matching more than one row,
            `{"ambiguous": True, "matches": [...]}` instead of guessing.
    """
    if entity_type == "candidate":
        if entity_id is None or "confidential" not in allowed_visibilities(role):
            return {}
        candidate = session.get(Candidate, entity_id)
        if candidate is None:
            return {}
        occupation = (
            session.get(Occupation, candidate.primary_occupation_id)
            if candidate.primary_occupation_id else None
        )
        skills = session.execute(
            select(Skill.name, CandidateSkill.proficiency)
            .join(CandidateSkill, CandidateSkill.skill_id == Skill.id)
            .where(CandidateSkill.candidate_id == entity_id)
        ).all()
        return {
            "candidate_id": entity_id, "full_name": candidate.full_name,
            "years_experience": candidate.years_experience,
            "target_occupation": occupation.title if occupation else None,
            "skills": [{"name": name, "proficiency": proficiency.value} for name, proficiency in skills],
        }

    if entity_type == "job":
        if entity_id is None:
            return {}
        job = session.get(Job, entity_id)
        if job is None:
            return {}
        skills = session.execute(
            select(Skill.name, JobSkill.requirement_level)
            .join(JobSkill, JobSkill.skill_id == Skill.id)
            .where(JobSkill.job_id == entity_id)
        ).all()
        return {
            "job_id": entity_id, "title": job.title, "company_name": job.company_name,
            "required_skills": [{"name": name, "level": level.value} for name, level in skills],
        }

    if entity_type == "skill":
        skill = None
        if entity_id is not None:
            skill = session.get(Skill, entity_id)
        elif entity_name is not None:
            skill = session.execute(select(Skill).where(Skill.name == entity_name)).scalar_one_or_none()
        if skill is None:
            return {}
        return {"skill_id": skill.id, "name": skill.name, "category": skill.category}

    if entity_type == "course":
        course = None
        if entity_id is not None:
            course = session.get(Course, entity_id)
        elif entity_name is not None:
            # Course.title has no uniqueness constraint (unlike Skill.name)
            # -- fail honest on a genuine ambiguity rather than silently
            # picking one and risking a wrong answer.
            matches = session.execute(select(Course).where(Course.title == entity_name)).scalars().all()
            if len(matches) > 1:
                return {
                    "ambiguous": True,
                    "matches": [{"course_id": c.id, "provider": c.provider} for c in matches],
                }
            course = matches[0] if matches else None
        if course is None:
            return {}
        skills = session.execute(
            select(Skill.name)
            .join(CourseSkill, CourseSkill.skill_id == Skill.id)
            .where(CourseSkill.course_id == course.id)
        ).scalars().all()
        return {
            "course_id": course.id, "title": course.title, "provider": course.provider,
            "level": course.level, "duration_hours": course.duration_hours,
            "taught_skills": sorted(skills),
        }

    return {}
