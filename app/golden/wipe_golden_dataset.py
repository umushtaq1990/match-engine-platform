"""golden-dataset v2: wipes the existing golden subset before rebuilding it
end to end under the new, correctly domain-anchored taxonomy -- the
existing set was selected under the old (randomly-anchored, title-
mismatched) scheme, so topping it up would leave a mixed-quality dataset
(see the golden-dataset-v2 plan's "full regenerate, not a top-up" note).

Deletes, in dependency order: golden_injected_noise rows, golden_pairs
rows, the Job/JobSkill rows those pairs referenced, the CandidateDocument
rows flagged as a golden CV, and the extraction_runs/
extraction_example_results rows whose source is golden_cv/golden_job (any
other eval history -- e.g. the model-comparison runs already analyzed in
docs/architecture/application-services/skill-extraction.md -- is left
untouched). Does NOT touch the base 2000-candidate seed pool, the new
curated occupations/occupation_skills (app/golden/
seed_curated_occupations.py), Keycloak logins, or behavioral events --
those are either out of scope or cheap/idempotent to just re-run after.

Does NOT delete the underlying SeaweedFS CV objects -- object_key() is
deterministic per candidate_id, so a candidate reselected as golden again
simply overwrites its old blob; a candidate not reselected leaves one
orphaned object, an accepted, low-cost tradeoff in this dev environment
rather than building a delete-from-SeaweedFS path for a one-off wipe.

Run with: docker compose run --rm golden-wipe
"""
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.extraction.models import CandidateDocument, ExtractionExampleResult, ExtractionRun
from app.golden.generate_cvs import GOLDEN_FILENAME
from app.golden.models import GoldenInjectedNoise, GoldenPair
from app.ingestion.source_models import Job, JobSkill

_GOLDEN_SOURCES = ("golden_cv", "golden_job")


def main() -> None:
    session = SessionLocal()
    try:
        job_ids = session.execute(select(GoldenPair.job_id)).scalars().all()

        noise_deleted = session.execute(delete(GoldenInjectedNoise)).rowcount
        pairs_deleted = session.execute(delete(GoldenPair)).rowcount

        job_skills_deleted = 0
        jobs_deleted = 0
        if job_ids:
            job_skills_deleted = session.execute(
                delete(JobSkill).where(JobSkill.job_id.in_(job_ids))
            ).rowcount
            jobs_deleted = session.execute(delete(Job).where(Job.id.in_(job_ids))).rowcount

        docs_deleted = session.execute(
            delete(CandidateDocument).where(CandidateDocument.original_filename == GOLDEN_FILENAME)
        ).rowcount

        golden_run_ids = session.execute(
            select(ExtractionRun.id).where(ExtractionRun.id.in_(
                select(ExtractionExampleResult.run_id).where(
                    ExtractionExampleResult.source.in_(_GOLDEN_SOURCES)
                ).distinct()
            ))
        ).scalars().all()
        results_deleted = session.execute(
            delete(ExtractionExampleResult).where(ExtractionExampleResult.source.in_(_GOLDEN_SOURCES))
        ).rowcount
        runs_deleted = 0
        if golden_run_ids:
            runs_deleted = session.execute(
                delete(ExtractionRun).where(ExtractionRun.id.in_(golden_run_ids))
            ).rowcount

        session.commit()
        print(f"golden_injected_noise deleted: {noise_deleted}")
        print(f"golden_pairs deleted: {pairs_deleted}")
        print(f"job_skills deleted: {job_skills_deleted}")
        print(f"jobs deleted: {jobs_deleted}")
        print(f"candidate_documents (golden CVs) deleted: {docs_deleted}")
        print(f"extraction_example_results (golden source) deleted: {results_deleted}")
        print(f"extraction_runs (now-empty golden runs) deleted: {runs_deleted}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
