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
"""
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


def build_golden_questions(
    session: Session, n_candidates: int = 10, n_jobs: int = 10,
    n_skills: int = 10, n_courses: int = 10,
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

    return questions
