"""Builds a small (question, expected_answer) ground-truth set for the
chatbot eval harness, from the real golden dataset's own structured facts
-- built independently of the retrieval/generation code under test, same
"avoid circular evaluation" discipline as `app.evaluation.ground_truth`
(whose own docstring states that principle directly). Programmatic, not
hand-authored prose, so it's repeatable and scales with the golden
dataset rather than going stale.
"""
from sqlalchemy.orm import Session

from app.golden.select import select_existing_golden_candidate_ids, select_existing_golden_job_ids
from app.ingestion.source_models import Candidate, Job, Occupation


def build_golden_questions(session: Session, n_candidates: int = 10, n_jobs: int = 10) -> list[dict]:
    """Builds factual Q&A pairs directly from golden candidates'/jobs'
    structured DB rows.

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

    return questions
