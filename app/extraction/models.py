"""ORM models for Use Case C (skill extraction): evaluation run results
(two-level pattern, same as app/evaluation/eval_models.py and
app/ingestion/quality_models.py — run summary + per-example detail) and CV
document metadata (CandidateDocument).

Lives in the public schema, not an isolated one like app/analytics/models.py
— see docs/skill-extraction.md "Service boundary": unlike behavioral
analytics, this is part of the core domain (its eventual purpose is to
populate the same job_skills/candidate_skills tables the matching engine
already owns), not a genuinely separate concern.
"""
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column()
    llm_model: Mapped[str] = mapped_column(String(100))
    embedding_model: Mapped[str] = mapped_column(String(100))
    similarity_threshold: Mapped[float] = mapped_column(Numeric(4, 3))
    prompt_version: Mapped[str] = mapped_column(String(50))
    sample_size: Mapped[int] = mapped_column()
    precision_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    recall_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    f1_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    requirement_level_accuracy_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    unmatched_phrase_rate_avg: Mapped[float] = mapped_column(Numeric(6, 4))

    example_results: Mapped[list["ExtractionExampleResult"]] = relationship(back_populates="run")


class ExtractionExampleResult(Base):
    __tablename__ = "extraction_example_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    # e.g. "job:17" (generator-injected) or "golden:cv_03" (hand-authored)
    example_id: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(20))
    precision: Mapped[float] = mapped_column(Numeric(6, 4))
    recall: Mapped[float] = mapped_column(Numeric(6, 4))
    f1: Mapped[float] = mapped_column(Numeric(6, 4))
    # Null when the example has no true positives to score agreement on.
    requirement_level_accuracy: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    expected_skill_ids: Mapped[list] = mapped_column(JSONB)
    predicted_skill_ids: Mapped[list] = mapped_column(JSONB)
    unmatched_phrases: Mapped[list] = mapped_column(JSONB)

    run: Mapped["ExtractionRun"] = relationship(back_populates="example_results")


class CandidateDocument(Base):
    """Metadata for a candidate's uploaded CV (object bytes live in
    SeaweedFS's S3-compatible object storage, never here — see
    app/extraction/document_storage.py and docs/skill-extraction.md "CV
    upload storage"). One row per candidate: a re-upload overwrites the
    prior object at the same key and updates this row in place, it doesn't
    accumulate a version history — a documented v1 simplification.
    """
    __tablename__ = "candidate_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), unique=True, index=True)
    object_key: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    uploaded_at: Mapped[datetime] = mapped_column()
