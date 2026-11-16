"""Golden job-posting generator: for each of the 500 golden candidates
(app/golden/select.py), creates 3 new job postings deliberately constructed
to be a near-perfect skill match for that candidate -- unlike
app/ingestion/generate_synthetic_data.py's `_build_job_description` (Faker
boilerplate + one fixed template sentence naming the required/preferred
skills verbatim), each posting's description is realistic LLM prose that
mentions those same skills the way a real recruiter would, not a template.

Each candidate's 3 jobs share the same *target* skill ground truth (derived
straight from their own CandidateSkill rows) but are independently written
(different company, different phrasing) -- 3 genuinely different postings
that should all still rank as strong matches, not 3 copies of one.

Run with: docker compose run --rm golden-jobs [--limit N]
          [--backend {ollama,vllm}] [--llm-model NAME]

golden-dataset v2: --backend vllm, same reasoning as generate_cvs.py.

Idempotent/resumable: tops a candidate back up to exactly 3 GoldenPair rows
rather than skipping them outright, so a killed/restarted run doesn't
duplicate work already done.
"""
import argparse
import logging
import re
from datetime import datetime, timezone

from faker import Faker
from sqlalchemy import select

from app.common.logging import setup_logging
from app.db import SessionLocal
from app.extraction.ollama_client import generate as _ollama_generate
from app.golden import vllm_client
from app.golden.llm_retry import make_generate_with_retry
from app.golden.models import GoldenPair
from app.golden.select import select_existing_golden_candidate_ids
from app.ingestion.source_models import (
    Candidate,
    CandidateSkill,
    EmploymentType,
    Job,
    JobSkill,
    Occupation,
    ProficiencyLevel,
    RequirementLevel,
    Skill,
)

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_MODEL = "qwen2.5:1.5b"
JOBS_PER_CANDIDATE = 3

_JOB_PROMPT = """Write a realistic job posting, as plain text (no Markdown formatting, no \
asterisks or headings). Write it the way a real recruiter would -- a short paragraph about the \
role and company, then naturally work in the required and preferred skills below in your own \
words (synonyms/natural phrasing are fine, this does not need to repeat the skill names \
verbatim as a bullet list).

Job facts:
- Title: {title}
- Company: {company}
- Minimum years of experience: {min_years_experience}
- Required skills: {required_skills}
- Preferred (nice-to-have) skills: {preferred_skills}

Job posting:
"""


def _strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return text


def _target_skills(session, candidate_id: int) -> list[tuple[int, str, str]]:
    """(skill_id, skill_name, requirement_level) derived straight from the
    candidate's own real skills -- intermediate+ proficiency becomes
    'required', beginner becomes 'preferred', so the job is a near-perfect
    match for this candidate by construction.
    """
    rows = session.execute(
        select(CandidateSkill.skill_id, Skill.name, CandidateSkill.proficiency)
        .join(Skill, Skill.id == CandidateSkill.skill_id)
        .where(CandidateSkill.candidate_id == candidate_id)
    ).all()
    result = []
    for skill_id, name, proficiency in rows:
        level = (
            RequirementLevel.preferred if proficiency == ProficiencyLevel.beginner
            else RequirementLevel.required
        )
        result.append((skill_id, name, level))
    return result


def _build_prompt(
    fake: Faker, title: str, min_years: int, target_skills: list[tuple[int, str, RequirementLevel]]
) -> tuple[str, str]:
    company = fake.company()
    required = [name for _, name, level in target_skills if level == RequirementLevel.required]
    preferred = [name for _, name, level in target_skills if level == RequirementLevel.preferred]
    prompt = _JOB_PROMPT.format(
        title=title, company=company, min_years_experience=min_years,
        required_skills=", ".join(required) or "none specified",
        preferred_skills=", ".join(preferred) or "none specified",
    )
    return prompt, company


def main(limit: int | None, backend: str, llm_model: str | None) -> None:
    backend_generate = vllm_client.generate if backend == "vllm" else _ollama_generate
    model = llm_model or (DEFAULT_OLLAMA_MODEL if backend == "ollama" else "unused-for-vllm")
    generate_with_retry = make_generate_with_retry(backend_generate)

    session = SessionLocal()
    fake = Faker()
    try:
        golden_ids = select_existing_golden_candidate_ids(session)
        if limit is not None:
            golden_ids = golden_ids[:limit]

        occupation_titles = dict(session.execute(select(Occupation.id, Occupation.title)).all())

        created = 0
        skipped = 0
        failed = 0
        for i, candidate_id in enumerate(golden_ids, start=1):
            existing_count = session.execute(
                select(GoldenPair).where(GoldenPair.candidate_id == candidate_id)
            ).scalars().all()
            missing = JOBS_PER_CANDIDATE - len(existing_count)
            if missing <= 0:
                skipped += 1
                continue

            candidate = session.get(Candidate, candidate_id)
            title = occupation_titles.get(candidate.primary_occupation_id, "Specialist")
            target_skills = _target_skills(session, candidate_id)

            candidate_failed = False
            for job_num in range(1, missing + 1):
                logger.info(
                    "generating golden job", extra={"candidate_id": candidate_id, "job_num": job_num}
                )
                try:
                    prompt, company = _build_prompt(fake, title, candidate.years_experience, target_skills)
                    description = _strip_markdown(generate_with_retry(model, prompt))

                    job = Job(
                        title=title,
                        company_name=company,
                        occupation_id=candidate.primary_occupation_id,
                        description_raw=description,
                        location_city=candidate.location_city,
                        location_region=candidate.location_region,
                        postal_code=candidate.postal_code,
                        country=candidate.country,
                        employment_type=EmploymentType.full_time,
                        posted_at=datetime.now(timezone.utc),
                        min_years_experience=min(candidate.years_experience, 10),
                    )
                    session.add(job)
                    session.flush()

                    for skill_id, _name, level in target_skills:
                        session.add(JobSkill(job_id=job.id, skill_id=skill_id, requirement_level=level))

                    session.add(GoldenPair(
                        candidate_id=candidate_id, job_id=job.id,
                        created_at=datetime.now(timezone.utc),
                    ))
                    session.commit()
                    created += 1
                except Exception:
                    # Same reasoning as generate_cvs.py: one candidate's
                    # persistent failure must not abort the whole batch --
                    # log it, roll back the half-built job, and move on. A
                    # future re-run tops this candidate back up to 3.
                    logger.warning(
                        "golden job generation failed, skipping remaining job(s)", exc_info=True,
                        extra={"candidate_id": candidate_id, "job_num": job_num},
                    )
                    session.rollback()
                    failed += 1
                    candidate_failed = True
                    break
            if candidate_failed:
                continue

            print(f"[{i}/{len(golden_ids)}] candidate {candidate_id}: {missing} golden job(s) created")

        print(
            f"Golden jobs created: {created}, candidates skipped (already had 3): {skipped}, "
            f"candidates failed (will retry on next run): {failed}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    setup_logging("golden-jobs")
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N golden candidates")
    parser.add_argument("--backend", choices=["ollama", "vllm"], default="ollama")
    parser.add_argument(
        "--llm-model", default=None,
        help="Ignored when --backend vllm (vLLM serves exactly one model per process).",
    )
    args = parser.parse_args()
    main(limit=args.limit, backend=args.backend, llm_model=args.llm_model)
