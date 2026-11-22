"""The two tools the chat agent (app/chatbot/graph.py) can call. Both query
the domain tables directly (`app.ingestion.source_models`), not
`app.golden.drilldown.data`'s loaders -- that module is this specific
Streamlit app's presentation layer; importing it here would tie this
otherwise-portable package to one UI, defeating the point of a decoupled
package a little duplication of a short query is the right tradeoff.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chatbot.access_control import allowed_visibilities
from app.chatbot.graph_retrieval import graph_context_for_candidate, graph_context_for_job
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
TOOL_LOOKUP_GRAPH_CONTEXT = "lookup_graph_context"
TOOL_RANK_BY_CRITERIA = "rank_by_criteria"

RANK_BY_CRITERIA_MAX_TOP_K = 20

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
                "entity_id": {
                    "type": "integer",
                    "description": "for candidate/job, named by id in the question",
                },
                "entity_name": {
                    "type": "string",
                    "description": "for skill/course, named by name/title in the question",
                },
            },
            "required": ["entity_type"],
        },
    },
]

# Kept out of TOOL_SPECS (offered unconditionally today) -- graph.py only
# appends this to the agent's tool list when `enable_graph_tool=True`, so
# the live chat page's 2-tool behavior stays unchanged by default (see the
# Phase 2 plan's "measure before adopting" decision).
GRAPH_TOOL_SPEC = {
    "name": TOOL_LOOKUP_GRAPH_CONTEXT,
    "description": (
        "Finds candidates/jobs/courses RELATED to one specific candidate or job via shared skills in "
        "the knowledge graph -- e.g. 'which other candidates are similar to candidate 7', 'what jobs "
        "match this candidate's skillset', 'what courses would help someone targeting job 42'. Use for "
        "similarity/relationship questions about one named candidate/job, NOT for that entity's own "
        "exact facts (use lookup_structured_data for those instead)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_type": {"type": "string", "enum": ["candidate", "job"]},
            "entity_id": {
                "type": "integer", "description": "the candidate or job's id, named in the question",
            },
        },
        "required": ["entity_type", "entity_id"],
    },
}

# Kept out of TOOL_SPECS, same opt-in discipline as GRAPH_TOOL_SPEC -- see
# graph.py's enable_rank_tool. Fills a real, previously-unanswerable gap:
# neither lookup_structured_data (one named id) nor lookup_graph_context
# (adjacent to one named id) can answer an open-ended, criteria-based
# question across the WHOLE candidate/job pool ("top candidates with
# Python and AWS experience") -- see the Roadmap entry this closes in
# docs/architecture/application-services/rag-chatbot.md.
RANK_TOOL_SPEC = {
    "name": TOOL_RANK_BY_CRITERIA,
    "description": (
        "Ranks ALL candidates or ALL jobs (not just ones related to one named id) by how well they "
        "match stated skill criteria -- e.g. 'top candidates with Python and AWS experience', 'which "
        "jobs need machine learning skills', 'candidates with at least 5 years of experience and SQL'. "
        "Use for open-ended search/ranking across the whole pool, NOT for 'similar to candidate/job X' "
        "(use lookup_graph_context for that) and NOT for one named candidate/job's own facts (use "
        "lookup_structured_data for that)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_type": {"type": "string", "enum": ["candidate", "job"]},
            "required_skills": {
                "type": "array", "items": {"type": "string"},
                "description": "skill names mentioned in the question, e.g. [\"Python\", \"AWS\"]",
            },
            "min_years_experience": {
                "type": "integer",
                "description": "candidate searches only; omit entirely for a job search or if unstated",
            },
            "top_k": {"type": "integer", "description": "how many ranked results to return, default 5"},
        },
        "required": ["entity_type", "required_skills"],
    },
}


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


def lookup_graph_context(session: Session, role: str, entity_type: str, entity_id: int | None) -> dict:
    """The `lookup_graph_context` tool's real implementation --
    `app.chatbot.graph_retrieval`'s bounded Cypher queries, gated the same
    way `lookup_structured_data`'s candidate branch is. Returns `{}` for a
    candidate `role` can't see, a missing entity, or an unrecognized
    `entity_type` -- fails closed, same discipline as every other tool here.
    """
    if entity_id is None:
        return {}
    if entity_type == "candidate":
        if "confidential" not in allowed_visibilities(role) or session.get(Candidate, entity_id) is None:
            return {}
        return graph_context_for_candidate(entity_id)
    if entity_type == "job":
        if session.get(Job, entity_id) is None:
            return {}
        return graph_context_for_job(entity_id)
    return {}


def rank_by_criteria(
    session: Session, role: str, entity_type: str, required_skills: list[str],
    min_years_experience: int | None = None, top_k: int = 5,
) -> dict:
    """The `rank_by_criteria` tool's real implementation -- an AND-ranked
    SQL query over `CandidateSkill`/`JobSkill`, scored by how many of
    `required_skills` each row actually has (not a fuzzy/semantic match --
    exact, case-insensitive skill-name matches only, same precision-over-
    recall choice `lookup_structured_data` already makes for exact lookups).
    Candidate results are gated exactly like `lookup_structured_data`'s
    candidate branch -- fails closed to `{}` for a role that can't see
    confidential data.

    Args:
        session: an active SQLAlchemy session.
        role: gates candidate results the same way every other tool here does.
        entity_type: `"candidate"` or `"job"`.
        required_skills: skill names to match against (unmatched names are
            silently dropped, not an error -- a question can name a skill
            that doesn't exist in this dataset).
        min_years_experience: candidate searches only; ignored for jobs
            (`Job` has no years-of-experience field).
        top_k: how many ranked rows to return, capped at
            `RANK_BY_CRITERIA_MAX_TOP_K` so a bad/huge argument from the
            model can't turn this into an unbounded result dump.

    Returns:
        dict: `{"entity_type": str, "matched_skill_names": list[str] (which
            of `required_skills` actually exist in this dataset), "ranked":
            list[dict]}`, each ranked row `{"id", "name"/"title",
            "matched_skill_count", "matched_skills": list[str], plus
            "years_experience" for a candidate or "company_name" for a job}`,
            ordered by `matched_skill_count` descending. `{"entity_type": ...,
            "matched_skill_names": [], "ranked": []}` if none of
            `required_skills` exist in this dataset, or `{}` for an
            unrecognized `entity_type` or a candidate search `role` can't see.
    """
    if entity_type == "candidate" and "confidential" not in allowed_visibilities(role):
        return {}
    if entity_type not in ("candidate", "job"):
        return {}

    top_k = max(1, min(top_k, RANK_BY_CRITERIA_MAX_TOP_K))
    skill_rows = session.execute(
        select(Skill.id, Skill.name).where(
            func.lower(Skill.name).in_([s.lower() for s in required_skills if s]),
        )
    ).all()
    matched_skill_names = sorted({name for _, name in skill_rows})
    if not skill_rows:
        return {"entity_type": entity_type, "matched_skill_names": [], "ranked": []}
    skill_ids = [skill_id for skill_id, _ in skill_rows]

    if entity_type == "candidate":
        query = (
            select(
                Candidate.id, Candidate.full_name, Candidate.years_experience,
                func.count(func.distinct(CandidateSkill.skill_id)).label("matched_count"),
                func.array_agg(func.distinct(Skill.name)).label("matched_skills"),
            )
            .join(CandidateSkill, CandidateSkill.candidate_id == Candidate.id)
            .join(Skill, Skill.id == CandidateSkill.skill_id)
            .where(CandidateSkill.skill_id.in_(skill_ids))
        )
        if min_years_experience is not None:
            query = query.where(Candidate.years_experience >= min_years_experience)
        rows = session.execute(
            query.group_by(Candidate.id, Candidate.full_name, Candidate.years_experience)
            .order_by(func.count(func.distinct(CandidateSkill.skill_id)).desc())
            .limit(top_k)
        ).all()
        ranked = [
            {
                "id": r.id, "name": r.full_name, "years_experience": r.years_experience,
                "matched_skill_count": r.matched_count, "matched_skills": sorted(r.matched_skills),
            }
            for r in rows
        ]
    else:
        rows = session.execute(
            select(
                Job.id, Job.title, Job.company_name,
                func.count(func.distinct(JobSkill.skill_id)).label("matched_count"),
                func.array_agg(func.distinct(Skill.name)).label("matched_skills"),
            )
            .join(JobSkill, JobSkill.job_id == Job.id)
            .join(Skill, Skill.id == JobSkill.skill_id)
            .where(JobSkill.skill_id.in_(skill_ids))
            .group_by(Job.id, Job.title, Job.company_name)
            .order_by(func.count(func.distinct(JobSkill.skill_id)).desc())
            .limit(top_k)
        ).all()
        ranked = [
            {
                "id": r.id, "title": r.title, "company_name": r.company_name,
                "matched_skill_count": r.matched_count, "matched_skills": sorted(r.matched_skills),
            }
            for r in rows
        ]

    return {"entity_type": entity_type, "matched_skill_names": matched_skill_names, "ranked": ranked}
