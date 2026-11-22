"""Builds a (question, expected_answer) ground-truth set for the chatbot
eval harness, from the real golden dataset's own structured facts --
built independently of the retrieval/generation code under test, same
"avoid circular evaluation" discipline as `app.evaluation.ground_truth`
(whose own docstring states that principle directly). Programmatic, not
hand-authored prose, so it's repeatable and scales with the golden
dataset rather than going stale.

Six question templates across all four chunk source types (candidate/job/
skill/course) -- the original four (years of experience, target
occupation, company, job title) only ever touched candidate/job chunks;
skills/courses chunks had zero eval coverage until this pass.

Phase 2 adds a 7th/8th template -- relationship questions ("which other
candidates share X's skills", "what jobs match X") that only
`lookup_graph_context` (app/chatbot/tools.py) can answer. Ground truth for
these is computed via plain SQL joins over CandidateSkill/JobSkill/
CourseSkill, independent of Neo4j/app.chatbot.graph_retrieval -- Neo4j's
graph is itself just a sync of these same relational facts, so this SQL
computation is legitimate ground truth, not a weaker stand-in, and keeps
the same "don't test code against itself" discipline as the rest of this
file.
"""
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.golden.select import select_existing_golden_candidate_ids, select_existing_golden_job_ids
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

_TOP_N_GRAPH = 3


def _top_shared_skill_ids(
    session: Session, center_skill_ids: set[int], other_rows: list[tuple[int, int]], exclude_id: int | None,
) -> list[int]:
    """`other_rows` is `[(other_entity_id, skill_id), ...]` -- one row per
    (entity, skill) pair across every candidate `other_entity_id`'s skills
    (or every job's required skills / course's taught skills). Counts
    overlap with `center_skill_ids` per entity, returns the top
    `_TOP_N_GRAPH` entity ids by shared-skill count (ties broken by id,
    for a deterministic expected answer).
    """
    counts: Counter[int] = Counter()
    for other_id, skill_id in other_rows:
        if other_id != exclude_id and skill_id in center_skill_ids:
            counts[other_id] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [entity_id for entity_id, _ in ranked[:_TOP_N_GRAPH]]


def _candidate_skill_ids(session: Session, candidate_id: int) -> set[int]:
    return set(session.execute(
        select(CandidateSkill.skill_id).where(CandidateSkill.candidate_id == candidate_id)
    ).scalars().all())


def _job_skill_ids(session: Session, job_id: int) -> set[int]:
    return set(session.execute(
        select(JobSkill.skill_id).where(JobSkill.job_id == job_id)
    ).scalars().all())


def _all_candidate_skill_rows(session: Session) -> list[tuple[int, int]]:
    return session.execute(select(CandidateSkill.candidate_id, CandidateSkill.skill_id)).all()


def _all_job_skill_rows(session: Session) -> list[tuple[int, int]]:
    return session.execute(select(JobSkill.job_id, JobSkill.skill_id)).all()


def _all_course_skill_rows(session: Session) -> list[tuple[int, int]]:
    return session.execute(select(CourseSkill.course_id, CourseSkill.skill_id)).all()


def _names_for(session: Session, model, ids: list[int], name_attr: str) -> list[str]:
    rows = {row.id: getattr(row, name_attr) for row in session.execute(
        select(model).where(model.id.in_(ids))
    ).scalars().all()} if ids else {}
    return [rows[i] for i in ids if i in rows]


def build_golden_questions(
    session: Session, n_candidates: int = 10, n_jobs: int = 10,
    n_skills: int = 10, n_courses: int = 10, n_candidate_graph: int = 5, n_job_graph: int = 5,
) -> list[dict]:
    """Builds factual Q&A pairs directly from golden candidates'/jobs'
    structured DB rows, plus the full skill/course catalog (not golden-
    sampled -- skills/courses aren't part of the golden-candidate/job
    curation, every row is real reference data).

    Returns:
        list[dict]: each `{"question": str, "expected_answer": str,
            "expected_source_type": str, "expected_source_entity_id": int}`
            -- the last two name which `DocumentChunk` the answer should
            come from, for retrieval-precision scoring.
    """
    questions = []

    candidate_ids = select_existing_golden_candidate_ids(session)[:n_candidates]
    for candidate_id in candidate_ids:
        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            continue
        questions.append({
            "question": f"How many years of experience does candidate {candidate_id} have?",
            "expected_answer": f"{candidate.years_experience} years of experience.",
            "expected_source_type": "candidate_profile",
            "expected_source_entity_id": candidate_id,
        })
        if candidate.primary_occupation_id is not None:
            occupation = session.get(Occupation, candidate.primary_occupation_id)
            if occupation is not None:
                questions.append({
                    "question": f"What occupation is candidate {candidate_id} targeting?",
                    "expected_answer": occupation.title,
                    "expected_source_type": "candidate_profile",
                    "expected_source_entity_id": candidate_id,
                })
        skill_names = session.execute(
            select(Skill.name)
            .join(CandidateSkill, CandidateSkill.skill_id == Skill.id)
            .where(CandidateSkill.candidate_id == candidate_id)
            .order_by(Skill.name)
        ).scalars().all()
        if skill_names:
            questions.append({
                "question": f"What skills does candidate {candidate_id} have?",
                "expected_answer": ", ".join(skill_names),
                "expected_source_type": "candidate_profile",
                "expected_source_entity_id": candidate_id,
            })

    job_ids = select_existing_golden_job_ids(session)[:n_jobs]
    for job_id in job_ids:
        job = session.get(Job, job_id)
        if job is None:
            continue
        questions.append({
            "question": f"What company posted job {job_id}?",
            "expected_answer": job.company_name,
            "expected_source_type": "job_description",
            "expected_source_entity_id": job_id,
        })
        questions.append({
            "question": f"What is the job title for job {job_id}?",
            "expected_answer": job.title,
            "expected_source_type": "job_description",
            "expected_source_entity_id": job_id,
        })
        required_skill_names = session.execute(
            select(Skill.name)
            .join(JobSkill, JobSkill.skill_id == Skill.id)
            .where(JobSkill.job_id == job_id)
            .order_by(Skill.name)
        ).scalars().all()
        if required_skill_names:
            questions.append({
                "question": f"What skills are required for job {job_id}?",
                "expected_answer": ", ".join(required_skill_names),
                "expected_source_type": "job_description",
                "expected_source_entity_id": job_id,
            })

    skill_ids = session.execute(select(Skill.id).order_by(Skill.id).limit(n_skills)).scalars().all()
    for skill_id in skill_ids:
        skill = session.get(Skill, skill_id)
        if skill is None or not skill.category:
            continue
        questions.append({
            "question": f"What category is the skill {skill.name} in?",
            "expected_answer": skill.category,
            "expected_source_type": "skill",
            "expected_source_entity_id": skill_id,
        })

    course_ids = session.execute(select(Course.id).order_by(Course.id).limit(n_courses)).scalars().all()
    for course_id in course_ids:
        course = session.get(Course, course_id)
        if course is None:
            continue
        questions.append({
            "question": f"Who provides the course '{course.title}' (course id {course_id})?",
            "expected_answer": course.provider,
            "expected_source_type": "course",
            "expected_source_entity_id": course_id,
        })
        taught_skill_names = session.execute(
            select(Skill.name)
            .join(CourseSkill, CourseSkill.skill_id == Skill.id)
            .where(CourseSkill.course_id == course_id)
            .order_by(Skill.name)
        ).scalars().all()
        if taught_skill_names:
            questions.append({
                "question": f"What skills does the course '{course.title}' (course id {course_id}) teach?",
                "expected_answer": ", ".join(taught_skill_names),
                "expected_source_type": "course",
                "expected_source_entity_id": course_id,
            })

    # Phase 2 relationship questions -- only answerable via
    # lookup_graph_context. `expected_entity_ids` (not just one id, unlike
    # every category above) is the top-N peer/job/course ids by
    # shared-skill count, for precision@k/recall@k scoring against that
    # tool's returned id lists (see run_chatbot_eval.py).
    all_candidate_skill_rows = _all_candidate_skill_rows(session)
    all_job_skill_rows = _all_job_skill_rows(session)
    all_course_skill_rows = _all_course_skill_rows(session)

    for candidate_id in candidate_ids[:n_candidate_graph]:
        candidate_skill_ids = _candidate_skill_ids(session, candidate_id)
        if not candidate_skill_ids:
            continue
        peer_ids = _top_shared_skill_ids(session, candidate_skill_ids, all_candidate_skill_rows, candidate_id)
        if peer_ids:
            peer_names = _names_for(session, Candidate, peer_ids, "full_name")
            questions.append({
                "question": f"Which other candidates share the most skills with candidate {candidate_id}?",
                "expected_answer": ", ".join(peer_names),
                "expected_source_type": "candidate_graph",
                "expected_source_entity_id": candidate_id,
                "expected_entity_ids": peer_ids,
                # Matches lookup_graph_context's candidate-branch result
                # key (app.chatbot.tools/graph_retrieval.py) that this
                # question's expected_entity_ids are scored against.
                "expected_result_key": "similar_candidates",
            })
        job_ids_for_candidate = _top_shared_skill_ids(session, candidate_skill_ids, all_job_skill_rows, None)
        if job_ids_for_candidate:
            job_titles = _names_for(session, Job, job_ids_for_candidate, "title")
            questions.append({
                "question": f"What jobs best match candidate {candidate_id}'s skillset?",
                "expected_answer": ", ".join(job_titles),
                "expected_source_type": "candidate_graph",
                "expected_source_entity_id": candidate_id,
                "expected_entity_ids": job_ids_for_candidate,
                "expected_result_key": "matching_jobs",
            })

    for job_id in job_ids[:n_job_graph]:
        job_skill_ids = _job_skill_ids(session, job_id)
        if not job_skill_ids:
            continue
        course_ids_for_job = _top_shared_skill_ids(session, job_skill_ids, all_course_skill_rows, None)
        if course_ids_for_job:
            course_titles = _names_for(session, Course, course_ids_for_job, "title")
            questions.append({
                "question": f"What courses would help someone targeting job {job_id}?",
                "expected_answer": ", ".join(course_titles),
                "expected_source_type": "job_graph",
                "expected_source_entity_id": job_id,
                "expected_entity_ids": course_ids_for_job,
                "expected_result_key": "relevant_courses",
            })

    return questions
