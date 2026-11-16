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

from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric, String, Text
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
    # Full per-stage trace, added so an AI engineer can see exactly where a
    # given example's errors come from -- LLM-stage (never extracted a
    # phrase for a real skill) vs embedding-stage (extracted fine, but
    # matched to the wrong skill, or the right skill scored below
    # similarity_threshold and got discarded) -- without this, only the
    # final skill_id sets survive, which conflates both failure modes into
    # one recall/precision number with no way to tell them apart.
    # Raw LLM output before any embedding matching: [{"phrase", "requirement_level"}, ...]
    extracted_phrases: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Successful matches with the detail predicted_skill_ids alone loses:
    # [{"skill_id", "requirement_level", "confidence", "source_phrase"}, ...]
    match_detail: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Extracted phrases that scored below similarity_threshold (real misses,
    # not the LLM's fault) -- same phrases as unmatched_phrases, plus the
    # actual score so a near-miss (0.53 when the threshold is 0.55) is
    # visibly different from a true non-match (0.1):
    # [{"phrase", "best_score"}, ...]
    unmatched_detail: Mapped[list | None] = mapped_column(JSONB, nullable=True)

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


class ExtractionCall(Base):
    """One row per real POST /extract call -- the production counterpart to
    ExtractionExampleResult, which only exists for the golden-eval harness.
    No ground truth exists here (real, unlabeled text), so there's no
    precision/recall/f1 to store -- document_confidence is a heuristic proxy
    (see pipeline._document_confidence), not a measured score. mlflow_trace_id
    links each row to its full per-phrase trace in the MLflow UI, so a human
    reviewer (or an engineer debugging a confidence dip) can go from "this
    document needs review" straight to "here's exactly what the LLM and the
    embedding step did" without re-running anything.
    """
    __tablename__ = "extraction_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    called_at: Mapped[datetime] = mapped_column(index=True)
    llm_model: Mapped[str] = mapped_column(String(100))
    embedding_model: Mapped[str] = mapped_column(String(100))
    similarity_threshold: Mapped[float] = mapped_column(Numeric(4, 3))
    # The submitted text itself -- needed for a human reviewer to judge a
    # flagged document at all, not just see that it was flagged. Same
    # sensitivity class as CandidateDocument's stored CVs (real user text),
    # kept here rather than only logged, consistent with that existing
    # choice elsewhere in this service.
    text: Mapped[str] = mapped_column(Text())
    text_length: Mapped[int] = mapped_column()
    match_count: Mapped[int] = mapped_column()
    unmatched_count: Mapped[int] = mapped_column()
    # Same shape as ExtractionExampleResult's match_detail/unmatched_detail
    # above -- this is what lets a reviewer (or the dashboard) see *what*
    # was extracted, not just how many phrases matched or didn't.
    match_detail: Mapped[list] = mapped_column(JSONB)
    unmatched_detail: Mapped[list] = mapped_column(JSONB)
    document_confidence: Mapped[float] = mapped_column(Numeric(6, 4))
    needs_review: Mapped[bool] = mapped_column(Boolean, index=True)
    mlflow_trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Set once a human has looked at a flagged document -- turns the
    # dashboard's "needs review" count into a real backlog metric (open vs.
    # closed) instead of just a running total that never comes back down.
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)
