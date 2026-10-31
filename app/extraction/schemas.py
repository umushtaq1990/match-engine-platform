"""Wire schemas for the skill-extraction API (Use Case C)."""
from datetime import datetime

from pydantic import BaseModel, Field

from app.extraction.pipeline import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LLM_MODEL,
    DEFAULT_SIMILARITY_THRESHOLD,
)


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1)
    llm_model: str = DEFAULT_LLM_MODEL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD


class SkillMatchOut(BaseModel):
    skill_id: int
    requirement_level: str
    confidence: float
    source_phrase: str


class UnmatchedPhraseOut(BaseModel):
    phrase: str
    best_score: float | None


class ExtractResponse(BaseModel):
    matches: list[SkillMatchOut]
    unmatched: list[UnmatchedPhraseOut]


class CandidateDocumentOut(BaseModel):
    candidate_id: int
    original_filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime

    model_config = {"from_attributes": True}
