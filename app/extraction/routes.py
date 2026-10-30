"""Skill-extraction endpoint (Use Case C, Phase 16) — thin HTTP layer over
app/extraction/pipeline.py. Stateless: text in, skills out, independent of
whether the source is a job description or a CV (see docs/skill-extraction.md
known limitations — no raw-CV-text field exists in the domain model yet).
"""
from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.extraction.deps import DbSession, RequireApiKey
from app.extraction.pipeline import extract_skills
from app.extraction.schemas import ExtractRequest, ExtractResponse

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
