"""Synthetic CV generator (follow-up to Phase 16's upload-only CV flow):
produces a realistic dummy PDF CV for every seeded candidate that doesn't
already have one, built from that candidate's own skills/occupation/
experience already in Postgres, and uploads it through the exact same
document_storage path a real user upload goes through (validate_cv_upload +
upload_cv) — the resulting candidate_documents rows are indistinguishable
from real uploads.

Run with: docker compose run --rm cv-seed (after `migrate` and `seed`)

Idempotent: a candidate that already has a candidate_documents row (a real
upload — e.g. the CI smoke test's CV for candidate 1 — or a prior run of
this script) is left untouched. Pass --force to regenerate every candidate's
CV from scratch instead.

Never committed to git: the generated PDF bytes live only in SeaweedFS, not
this repo — see docs/architecture/application-services/skill-extraction.md
"Synthetic CVs". Anyone who needs the dataset runs this job themselves.
"""
import argparse
from datetime import datetime, timezone

from fpdf import FPDF
from sqlalchemy import select

from app.db import SessionLocal
from app.extraction.document_storage import upload_cv, validate_cv_upload
from app.extraction.models import CandidateDocument
from app.ingestion.source_models import Candidate, CandidateSkill, Occupation, Skill


def _render_cv_pdf(
    full_name: str,
    email: str,
    city: str,
    region: str,
    years_experience: int,
    occupation_title: str | None,
    skills: list[tuple[str, str]],
) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, full_name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, email, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"{city}, {region}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    summary = f"{years_experience} years of experience"
    summary += f" in {occupation_title}." if occupation_title else "."
    pdf.multi_cell(0, 6, summary)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Skills", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    for name, proficiency in skills:
        pdf.cell(0, 6, f"- {name} ({proficiency})", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


def main(force: bool) -> None:
    session = SessionLocal()
    try:
        existing_candidate_ids = {
            row[0] for row in session.execute(select(CandidateDocument.candidate_id)).all()
        }
        occupation_titles = dict(session.execute(select(Occupation.id, Occupation.title)).all())

        skills_by_candidate: dict[int, list[tuple[str, str]]] = {}
        rows = session.execute(
            select(CandidateSkill.candidate_id, Skill.name, CandidateSkill.proficiency).join(
                Skill, Skill.id == CandidateSkill.skill_id
            )
        ).all()
        for candidate_id, skill_name, proficiency in rows:
            skills_by_candidate.setdefault(candidate_id, []).append((skill_name, proficiency.value))

        candidates = session.execute(select(Candidate)).scalars().all()

        generated = 0
        skipped = 0
        for candidate in candidates:
            if candidate.id in existing_candidate_ids and not force:
                skipped += 1
                continue

            pdf_bytes = _render_cv_pdf(
                full_name=candidate.full_name,
                email=candidate.email,
                city=candidate.location_city,
                region=candidate.location_region,
                years_experience=candidate.years_experience,
                occupation_title=occupation_titles.get(candidate.primary_occupation_id),
                skills=sorted(skills_by_candidate.get(candidate.id, [])),
            )
            validate_cv_upload("application/pdf", len(pdf_bytes), pdf_bytes[:5])
            key = upload_cv(candidate.id, pdf_bytes)

            doc = session.execute(
                select(CandidateDocument).where(CandidateDocument.candidate_id == candidate.id)
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if doc is None:
                doc = CandidateDocument(
                    candidate_id=candidate.id,
                    object_key=key,
                    original_filename="synthetic_cv.pdf",
                    content_type="application/pdf",
                    size_bytes=len(pdf_bytes),
                    uploaded_at=now,
                )
                session.add(doc)
            else:
                doc.object_key = key
                doc.original_filename = "synthetic_cv.pdf"
                doc.content_type = "application/pdf"
                doc.size_bytes = len(pdf_bytes)
                doc.uploaded_at = now
            session.commit()
            generated += 1

        print(f"Synthetic CVs generated: {generated}, skipped (already had a CV): {skipped}")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even candidates that already have a CV"
    )
    args = parser.parse_args()
    main(force=args.force)
