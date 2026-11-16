"""Backs up the expensive-to-regenerate part of the golden dataset -- the
500 LLM-written CVs and 1500 LLM-written job postings -- to a local
directory, so a wiped Docker volume (or a fresh clone) doesn't have to pay
for ~2,000 Ollama calls again. Logins (golden-logins) and behavioral
history (golden-events) are deliberately NOT backed up here: both are fast,
deterministic, non-LLM operations (Keycloak admin calls / seeded RNG), so
re-running those scripts after a restore is cheaper than backing them up.

Writes to data/golden/backup/ (git-ignored -- see .gitignore):
  - manifest.json           -- counts + export timestamp
  - cvs/<candidate_id>.pdf   -- the actual CV bytes from object storage
  - cvs_manifest.json        -- CandidateDocument metadata per candidate
  - jobs.json                -- each golden job's full Job/JobSkill/GoldenPair data

Run with: docker compose run --rm golden-export
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.extraction.document_storage import download_cv
from app.extraction.models import CandidateDocument
from app.golden.generate_cvs import GOLDEN_FILENAME
from app.golden.models import GoldenPair
from app.golden.select import select_golden_candidate_ids
from app.ingestion.source_models import Job, JobSkill

BACKUP_DIR = Path("data/golden/backup")


def _export_cvs(session, golden_ids: list[int]) -> list[dict]:
    cv_dir = BACKUP_DIR / "cvs"
    cv_dir.mkdir(parents=True, exist_ok=True)

    docs = session.execute(
        select(CandidateDocument).where(
            CandidateDocument.candidate_id.in_(golden_ids),
            CandidateDocument.original_filename == GOLDEN_FILENAME,
        )
    ).scalars().all()

    manifest = []
    for doc in docs:
        pdf_bytes = download_cv(doc.candidate_id)
        (cv_dir / f"{doc.candidate_id}.pdf").write_bytes(pdf_bytes)
        manifest.append({
            "candidate_id": doc.candidate_id,
            "original_filename": doc.original_filename,
            "content_type": doc.content_type,
            "size_bytes": doc.size_bytes,
        })
    return manifest


def _export_jobs(session, golden_ids: list[int]) -> list[dict]:
    pairs = session.execute(
        select(GoldenPair).where(GoldenPair.candidate_id.in_(golden_ids))
    ).scalars().all()

    entries = []
    for pair in pairs:
        job = session.get(Job, pair.job_id)
        job_skills = session.execute(
            select(JobSkill).where(JobSkill.job_id == job.id)
        ).scalars().all()
        entries.append({
            "candidate_id": pair.candidate_id,
            "job": {
                "title": job.title,
                "company_name": job.company_name,
                "occupation_id": job.occupation_id,
                "description_raw": job.description_raw,
                "location_city": job.location_city,
                "location_region": job.location_region,
                "postal_code": job.postal_code,
                "country": job.country,
                "employment_type": job.employment_type.value,
                "min_years_experience": job.min_years_experience,
            },
            "job_skills": [
                {"skill_id": js.skill_id, "requirement_level": js.requirement_level.value}
                for js in job_skills
            ],
        })
    return entries


def main() -> None:
    session = SessionLocal()
    try:
        golden_ids = select_golden_candidate_ids(session)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        cvs_manifest = _export_cvs(session, golden_ids)
        (BACKUP_DIR / "cvs_manifest.json").write_text(json.dumps(cvs_manifest, indent=2))

        jobs = _export_jobs(session, golden_ids)
        (BACKUP_DIR / "jobs.json").write_text(json.dumps(jobs, indent=2))

        manifest = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "golden_candidate_count": len(golden_ids),
            "cvs_exported": len(cvs_manifest),
            "jobs_exported": len(jobs),
        }
        (BACKUP_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))

        print(f"Exported {len(cvs_manifest)} CVs and {len(jobs)} jobs to {BACKUP_DIR}/")
    finally:
        session.close()


if __name__ == "__main__":
    main()
