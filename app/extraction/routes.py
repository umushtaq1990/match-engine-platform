"""Skill-extraction endpoint (Use Case C) — thin HTTP layer over
app/extraction/pipeline.py. Stateless: text in, skills out, independent of
whether the source is a job description or a CV.

Also exposes basic CV document upload/download (app/extraction/
document_storage.py) — a candidate's own CV, stored as-is in object
storage. Deliberately not wired into extract_skills automatically in v1:
parsing a PDF's raw text (scanned/image-only PDFs can't be parsed at all)
is a real, separate concern, not something to bolt on silently — see
docs/skill-extraction.md "CV upload storage" for the v2 roadmap note.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction import document_storage
from app.extraction.deps import DbSession, RequireApiKey
from app.extraction.models import CandidateDocument
from app.extraction.pipeline import extract_skills
from app.extraction.schemas import CandidateDocumentOut, ExtractRequest, ExtractResponse
from app.ingestion.source_models import Candidate

router = APIRouter(dependencies=[RequireApiKey])


@router.post("/extract", response_model=ExtractResponse)
def extract(payload: ExtractRequest, db: Session = DbSession):
    result = extract_skills(
        payload.text,
        db,
        llm_model=payload.llm_model,
        embedding_model=payload.embedding_model,
        similarity_threshold=payload.similarity_threshold,
    )
    return ExtractResponse(**result)


@router.post("/candidates/{candidate_id}/cv", response_model=CandidateDocumentOut, status_code=201)
def upload_cv(candidate_id: int, db: Session = DbSession, file: UploadFile = File(...)):
    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(status_code=404, detail=f"No candidate with id={candidate_id}")

    data = file.file.read(document_storage.MAX_CV_SIZE_BYTES + 1)
    try:
        document_storage.validate_cv_upload(file.content_type, len(data), data[:5])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        object_key = document_storage.upload_cv(candidate_id, data)
    except document_storage.StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"CV storage unavailable: {exc}") from exc

    existing = db.execute(
        select(CandidateDocument).where(CandidateDocument.candidate_id == candidate_id)
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.object_key = object_key
        existing.original_filename = file.filename or "cv.pdf"
        existing.content_type = file.content_type
        existing.size_bytes = len(data)
        existing.uploaded_at = now
        row = existing
    else:
        row = CandidateDocument(
            candidate_id=candidate_id, object_key=object_key,
            original_filename=file.filename or "cv.pdf", content_type=file.content_type,
            size_bytes=len(data), uploaded_at=now,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/candidates/{candidate_id}/cv", response_model=CandidateDocumentOut)
def get_cv_metadata(candidate_id: int, db: Session = DbSession):
    row = db.execute(
        select(CandidateDocument).where(CandidateDocument.candidate_id == candidate_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No CV on file for candidate_id={candidate_id}")
    return row


@router.get("/candidates/{candidate_id}/cv/download")
def download_cv(candidate_id: int, db: Session = DbSession):
    row = db.execute(
        select(CandidateDocument).where(CandidateDocument.candidate_id == candidate_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No CV on file for candidate_id={candidate_id}")
    try:
        data = document_storage.download_cv(candidate_id)
    except document_storage.StorageUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"CV storage unavailable: {exc}") from exc
    return Response(
        content=data, media_type=row.content_type,
        headers={"Content-Disposition": f'attachment; filename="{row.original_filename}"'},
    )
