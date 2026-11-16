"""Skill-extraction endpoint (Use Case C) — thin HTTP layer over
app/extraction/pipeline.py. The extraction itself is stateless (text in,
skills out, independent of whether the source is a job description or a
CV), but every call is logged to extraction_calls (app/extraction/models.py)
for the human-in-the-loop review queue below -- there's no ground truth for
real production text, so document_confidence is a heuristic proxy, not a
measured score (see pipeline._document_confidence for how it was derived
and validated against the golden-eval dataset).

Also exposes basic CV document upload/download (app/extraction/
document_storage.py) — a candidate's own CV, stored as-is in object
storage. Deliberately not wired into extract_skills automatically in v1:
parsing a PDF's raw text (scanned/image-only PDFs can't be parsed at all)
is a real, separate concern, not something to bolt on silently — see
docs/skill-extraction.md "CV upload storage" for the v2 roadmap note.
"""
from datetime import datetime, timezone

import mlflow
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction import document_storage
from app.extraction.deps import DbSession, RequireApiKey
from app.extraction.models import CandidateDocument, ExtractionCall
from app.extraction.pipeline import extract_skills
from app.extraction.schemas import (
    CandidateDocumentOut,
    ExtractionCallOut,
    ExtractRequest,
    ExtractResponse,
    ReviewRequest,
)
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

    # Best-effort, same reasoning as pipeline.py's tracing hooks -- logging
    # this row must never break a real extraction response. Not
    # get_current_active_span(): extract_skills's own @mlflow.trace span has
    # already closed by the time control returns here, so that would always
    # be None -- get_last_active_trace_id() is mlflow's API for exactly this
    # "grab the trace id of the call I just made" pattern (confirmed live).
    trace_id = None
    try:
        trace_id = mlflow.get_last_active_trace_id()
    except Exception:
        pass

    db.add(ExtractionCall(
        called_at=datetime.now(timezone.utc),
        llm_model=payload.llm_model,
        embedding_model=payload.embedding_model,
        similarity_threshold=payload.similarity_threshold,
        text_length=len(payload.text),
        match_count=len(result["matches"]),
        unmatched_count=len(result["unmatched"]),
        document_confidence=result["document_confidence"],
        needs_review=result["needs_review"],
        mlflow_trace_id=trace_id,
    ))
    db.commit()

    return ExtractResponse(**result)


@router.get("/review-queue", response_model=list[ExtractionCallOut])
def list_review_queue(db: Session = DbSession, limit: int = 100):
    """Open backlog: flagged extraction calls nobody has reviewed yet, most
    recent first. The daily-count/trend view belongs on the Grafana
    dashboard (it already owns that kind of aggregate); this is the detail
    list a human actually works through, with mlflow_trace_id as the jump-off
    point into that document's full per-phrase trace."""
    rows = db.execute(
        select(ExtractionCall)
        .where(ExtractionCall.needs_review.is_(True), ExtractionCall.reviewed_at.is_(None))
        .order_by(ExtractionCall.called_at.desc())
        .limit(limit)
    ).scalars().all()
    return rows


@router.post("/review-queue/{call_id}/review", response_model=ExtractionCallOut)
def mark_reviewed(call_id: int, payload: ReviewRequest, db: Session = DbSession):
    row = db.get(ExtractionCall, call_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No extraction call with id={call_id}")
    row.reviewed_at = datetime.now(timezone.utc)
    row.reviewer = payload.reviewer
    db.commit()
    db.refresh(row)
    return row


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
