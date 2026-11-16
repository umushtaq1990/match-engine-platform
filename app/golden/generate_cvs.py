"""Golden CV generator: unlike app/extraction/generate_synthetic_cvs.py's
templated FPDF (built mechanically from a candidate's own DB fields -- easy
for the extraction pipeline to parse because the text *is* the structured
data), this writes a realistic one-page resume in natural LLM prose for each
of the 500 golden candidates (app/golden/select.py), then renders and
uploads it through the exact same real path a user upload goes through.

Run with: docker compose run --rm golden-cvs [--limit N] [--force]

Idempotent/resumable via a `CandidateDocument.original_filename ==
"golden_cv.pdf"` marker -- a killed/restarted run skips candidates that
already have their golden CV uploaded, unless --force.
"""
import argparse
import logging
import re
from datetime import datetime, timezone

from fpdf import FPDF
from sqlalchemy import select

from app.common.logging import setup_logging
from app.db import SessionLocal
from app.extraction.document_storage import upload_cv, validate_cv_upload
from app.extraction.models import CandidateDocument
from app.golden.llm_retry import generate_with_retry
from app.golden.select import select_golden_candidate_ids
from app.ingestion.source_models import Candidate, CandidateSkill, Occupation, Skill

logger = logging.getLogger(__name__)

LLM_MODEL = "qwen2.5:1.5b"
GOLDEN_FILENAME = "golden_cv.pdf"

_CV_PROMPT = """Write a realistic one-page resume for a job candidate, as plain text (no \
Markdown formatting, no asterisks or headings). Do not use a rigid template or a "Skills:" \
bullet list -- write it the way a real person's resume reads, with a short summary and 2-3 \
experience bullet points that naturally mention their skills and seniority with each one, in \
your own words and phrasing.

Candidate facts (incorporate all of them naturally, do not just restate this list):
- Name: {full_name}
- Years of experience: {years_experience}
- Occupation: {occupation_title}
- Location: {city}, {region}
- Skills (name, proficiency): {skills}

Resume:
"""


def _sanitize_for_core_font(text: str) -> str:
    """fpdf2's core "Helvetica" font only supports latin-1 -- LLM output
    routinely includes unicode bullets/smart quotes/dashes that latin-1
    can't encode at all. Map the common ones to ASCII equivalents rather
    than pulling in a full Unicode font just for this.
    """
    replacements = {
        "•": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "…": "...",
    }
    for unicode_char, ascii_char in replacements.items():
        text = text.replace(unicode_char, ascii_char)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _strip_markdown(text: str) -> str:
    """The LLM writes Markdown (**bold**, # headings) even though the
    rendering target is a plain-text PDF cell, not a Markdown renderer --
    strip the emphasis/heading markers rather than printing literal
    asterisks and hashes.
    """
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return text


def _render_cv_pdf(full_name: str, email: str, body_text: str) -> bytes:
    body_text = _sanitize_for_core_font(_strip_markdown(body_text))
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _sanitize_for_core_font(full_name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, _sanitize_for_core_font(email), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, body_text)

    return bytes(pdf.output())


def _build_prompt(candidate, occupation_title, skills: list[tuple[str, str]]) -> str:
    skills_text = "; ".join(f"{name} ({proficiency})" for name, proficiency in sorted(skills))
    return _CV_PROMPT.format(
        full_name=candidate.full_name,
        years_experience=candidate.years_experience,
        occupation_title=occupation_title or "generalist",
        city=candidate.location_city,
        region=candidate.location_region,
        skills=skills_text,
    )


def main(limit: int | None, force: bool) -> None:
    session = SessionLocal()
    try:
        golden_ids = select_golden_candidate_ids(session)
        if limit is not None:
            golden_ids = golden_ids[:limit]

        already_done = {
            row[0] for row in session.execute(
                select(CandidateDocument.candidate_id).where(
                    CandidateDocument.original_filename == GOLDEN_FILENAME
                )
            ).all()
        }
        occupation_titles = dict(session.execute(select(Occupation.id, Occupation.title)).all())

        generated = 0
        skipped = 0
        failed = 0
        for candidate_id in golden_ids:
            if candidate_id in already_done and not force:
                skipped += 1
                continue

            logger.info("generating golden CV", extra={"candidate_id": candidate_id})
            try:
                candidate = session.get(Candidate, candidate_id)
                skills = [
                    (skill_name, proficiency.value)
                    for _, skill_name, proficiency in session.execute(
                        select(CandidateSkill.candidate_id, Skill.name, CandidateSkill.proficiency)
                        .join(Skill, Skill.id == CandidateSkill.skill_id)
                        .where(CandidateSkill.candidate_id == candidate_id)
                    ).all()
                ]

                prompt = _build_prompt(
                    candidate, occupation_titles.get(candidate.primary_occupation_id), skills
                )
                body_text = generate_with_retry(LLM_MODEL, prompt)
                pdf_bytes = _render_cv_pdf(candidate.full_name, candidate.email, body_text)

                validate_cv_upload("application/pdf", len(pdf_bytes), pdf_bytes[:5])
                key = upload_cv(candidate.id, pdf_bytes)
            except Exception:
                # One candidate's persistent failure (e.g. Ollama still
                # unreachable after the retries in generate_with_retry)
                # must not abort the whole multi-hour batch -- log it and
                # move on; a future re-run will retry this candidate since
                # it never got a CandidateDocument row.
                logger.warning(
                    "golden CV generation failed, skipping", exc_info=True,
                    extra={"candidate_id": candidate_id},
                )
                failed += 1
                session.rollback()
                continue

            doc = session.execute(
                select(CandidateDocument).where(CandidateDocument.candidate_id == candidate.id)
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if doc is None:
                doc = CandidateDocument(
                    candidate_id=candidate.id, object_key=key, original_filename=GOLDEN_FILENAME,
                    content_type="application/pdf", size_bytes=len(pdf_bytes), uploaded_at=now,
                )
                session.add(doc)
            else:
                doc.object_key = key
                doc.original_filename = GOLDEN_FILENAME
                doc.content_type = "application/pdf"
                doc.size_bytes = len(pdf_bytes)
                doc.uploaded_at = now
            session.commit()
            generated += 1
            print(f"[{generated + skipped}/{len(golden_ids)}] candidate {candidate_id}: golden CV uploaded")

        print(
            f"Golden CVs generated: {generated}, skipped (already had one): {skipped}, "
            f"failed (will retry on next run): {failed}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    setup_logging("golden-cvs")
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N golden candidates")
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even candidates that already have a golden CV"
    )
    args = parser.parse_args()
    main(limit=args.limit, force=args.force)
