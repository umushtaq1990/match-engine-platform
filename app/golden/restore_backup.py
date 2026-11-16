"""Restores the golden CVs/jobs backed up by export_backup.py -- used after
a wiped Docker volume (or on a fresh clone with the backup copied over) to
recover the expensive, LLM-generated part of the golden dataset without
calling Ollama at all. Idempotent: skips any candidate that already has its
golden CV / already has 3 GoldenPair rows, same markers generate_cvs.py and
generate_jobs.py use, so it's safe to run alongside (or instead of) them.

This assumes the BASE dataset (the 2000 seeded candidates/skills) and the
golden_pairs table (Alembic migration) already exist -- restoring those is
out of scope here (see app/ingestion/generate_synthetic_data.py and
alembic upgrade head). Logins (golden-logins) and behavioral history
(golden-events) are NOT restored here either -- both are fast/deterministic
to regenerate directly: run `docker compose run --rm golden-logins` then
`golden-events` after this script finishes.

Run with: docker compose run --rm golden-restore
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.extraction.document_storage import upload_cv, validate_cv_upload
from app.extraction.models import CandidateDocument
from app.golden.models import GoldenPair
from app.ingestion.source_models import EmploymentType, Job, JobSkill, RequirementLevel

BACKUP_DIR = Path("data/golden/backup")


def _restore_cvs(session) -> tuple[int, int]:
    manifest = json.loads((BACKUP_DIR / "cvs_manifest.json").read_text())
    cv_dir = BACKUP_DIR / "cvs"

    already_done = {
        row[0] for row in session.execute(
            select(CandidateDocument.candidate_id).where(
                CandidateDocument.original_filename == "golden_cv.pdf"
            )
        ).all()
    }

    restored = skipped = 0
    for entry in manifest:
        candidate_id = entry["candidate_id"]
        if candidate_id in already_done:
            skipped += 1
            continue

        pdf_bytes = (cv_dir / f"{candidate_id}.pdf").read_bytes()
        validate_cv_upload("application/pdf", len(pdf_bytes), pdf_bytes[:5])
        key = upload_cv(candidate_id, pdf_bytes)

        session.add(CandidateDocument(
            candidate_id=candidate_id, object_key=key,
            original_filename=entry["original_filename"],
            content_type=entry["content_type"], size_bytes=entry["size_bytes"],
            uploaded_at=datetime.now(timezone.utc),
        ))
        session.commit()
        restored += 1
    return restored, skipped


def _restore_jobs(session) -> tuple[int, int]:
    entries = json.loads((BACKUP_DIR / "jobs.json").read_text())

    existing_counts: dict[int, int] = {}
    for (candidate_id,) in session.execute(select(GoldenPair.candidate_id)).all():
        existing_counts[candidate_id] = existing_counts.get(candidate_id, 0) + 1

    restored = skipped = 0
    for entry in entries:
        candidate_id = entry["candidate_id"]
        if existing_counts.get(candidate_id, 0) >= 3:
            skipped += 1
            continue

        job_data = entry["job"]
        job = Job(
            title=job_data["title"],
            company_name=job_data["company_name"],
            occupation_id=job_data["occupation_id"],
            description_raw=job_data["description_raw"],
            location_city=job_data["location_city"],
            location_region=job_data["location_region"],
            postal_code=job_data["postal_code"],
            country=job_data["country"],
            employment_type=EmploymentType(job_data["employment_type"]),
            posted_at=datetime.now(timezone.utc),
            min_years_experience=job_data["min_years_experience"],
        )
        session.add(job)
        session.flush()

        for js in entry["job_skills"]:
            session.add(JobSkill(
                job_id=job.id, skill_id=js["skill_id"],
                requirement_level=RequirementLevel(js["requirement_level"]),
            ))

        session.add(GoldenPair(
            candidate_id=candidate_id, job_id=job.id,
            created_at=datetime.now(timezone.utc),
        ))
        session.commit()
        existing_counts[candidate_id] = existing_counts.get(candidate_id, 0) + 1
        restored += 1
    return restored, skipped


def main() -> None:
    if not (BACKUP_DIR / "manifest.json").exists():
        print(
            f"No backup found at {BACKUP_DIR}/ -- run golden-export first "
            "(on a machine that has the data)."
        )
        return

    session = SessionLocal()
    try:
        cvs_restored, cvs_skipped = _restore_cvs(session)
        print(f"CVs restored: {cvs_restored}, skipped (already present): {cvs_skipped}")

        jobs_restored, jobs_skipped = _restore_jobs(session)
        print(f"Jobs restored: {jobs_restored}, candidates skipped (already had 3): {jobs_skipped}")

        print(
            "Note: golden logins and behavioral events are not part of this backup "
            "(fast/deterministic to regenerate) -- run `docker compose run --rm golden-logins` "
            "then `golden-events` next."
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
