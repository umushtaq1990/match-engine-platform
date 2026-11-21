"""RAG chatbot data models (Phase 48) -- a self-contained, decoupled package
(see app/chatbot/__init__.py's absence of any import from app.golden.drilldown
or app.frontend: this package is meant to be liftable into another project
as-is, the same way app.embeddings/app.matching already are).

Chunk *embeddings* deliberately do NOT get a new vector column here --
they're stored in app.embeddings.models.EntityEmbedding with
entity_type="document_chunk", dimension="chunk", reusing that table's own
documented generic design rather than duplicating pgvector infra. See
app/chatbot/chunk_embeddings.py for why that's a small standalone refresh
function, not a registration into app.embeddings.compute_embeddings's
existing candidate/job dimension-dispatch loop (that loop assumes every
entity type shares the same three dimensions -- skills/experience/overall
-- which a document chunk, with exactly one dimension of its own, doesn't).
"""
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Matches app.embeddings.models.PRODUCTION_VECTOR_DIM's current model
# (all-minilm) -- chunks are embedded with the same model/table, not a
# separate one, so retrieval can compare query and chunk vectors directly.
DEFAULT_CHUNK_EMBEDDING_MODEL = "all-minilm"

VISIBILITY_PUBLIC = "public"
VISIBILITY_CONFIDENTIAL = "confidential"


class DocumentChunk(Base):
    """One retrievable unit of knowledge-base text. `source_type`/
    `source_entity_id` trace a chunk back to the domain row it was built
    from (e.g. `source_type="job_description"`, `source_entity_id=<job.id>`)
    -- the traceability the chatbot's "Sources" panel displays.

    `content_hash` + `updated_at` mirror app.embeddings.models.
    EntityEmbedding.source_hash and app.golden.models.GraphNarrative.
    facts_hash -- the same "recompute hash, only redo work if it changed"
    freshness discipline already used twice elsewhere in this project, not
    a time-based guess at staleness.

    `tags` is a free JSONB list (e.g. `["job", "engineering", "2026-q4"]`)
    for filtering, independent of `visibility`, which gates WHO can see a
    chunk rather than WHAT it's about.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_document_chunks_source", "source_type", "source_entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(30))
    source_entity_id: Mapped[int] = mapped_column()
    chunk_index: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text())
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    visibility: Mapped[str] = mapped_column(String(20), default=VISIBILITY_PUBLIC)
    content_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column()


class ChatSession(Base):
    """One chat conversation. `role` is a snapshot of the user's role at
    session start (not re-checked per message) -- simple and sufficient
    for this phase; a role change mid-session would need a fresh session
    to take effect, a documented v1 limitation, not an oversight.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column()


class ChatMessage(Base):
    """One turn in a ChatSession. `retrieved_chunk_ids` is the traceability
    record for an assistant message -- exactly which chunks the answer was
    grounded in, empty for a user message.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_session", "session_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text())
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column()


FEEDBACK_UP = "up"
FEEDBACK_DOWN = "down"


class ChatMessageFeedback(Base):
    """Thumbs up/down (+ optional comment, standard practice only on a down
    vote) on one specific assistant `ChatMessage` -- the standard per-message
    feedback pattern (ChatGPT/Claude/Copilot all use this, not an end-of-
    session survey), chosen specifically so a flagged answer can be traced
    back to its exact question/context, not just "something in this session
    was bad." One row per message (`UniqueConstraint` below) -- re-rating
    the same message upserts rather than appending a second row.
    """

    __tablename__ = "chat_message_feedback"
    __table_args__ = (UniqueConstraint("message_id", name="uq_chat_message_feedback_message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"))
    rating: Mapped[str] = mapped_column(String(10))
    comment: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column()


class ChatEvalResult(Base):
    """One golden-question's scored run (app/chatbot/eval/run_chatbot_eval.py)
    -- the per-question drill-down detail table, same two-tier shape
    (MLflow for run-level aggregates, Postgres for per-item detail) as
    app.evaluation.eval_models.EvaluationRun/EvaluationCandidateResult and
    app.embeddings.models.EmbeddingEvalResult.

    `retrieval_mode` is `"vector"` for every row this phase produces --
    the column exists now so Phase 2 ("graph") and Phase 3 ("hybrid") add
    rows under the same schema for direct comparison, not a new table.
    """

    __tablename__ = "chat_eval_results"
    __table_args__ = (UniqueConstraint("run_at", "question", "retrieval_mode", name="uq_chat_eval_result"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column()
    question: Mapped[str] = mapped_column(Text())
    expected_answer: Mapped[str] = mapped_column(Text())
    retrieval_mode: Mapped[str] = mapped_column(String(20))
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSONB, default=list)
    generated_answer: Mapped[str] = mapped_column(Text())
    relevance_score: Mapped[float] = mapped_column()
    coherence_score: Mapped[float] = mapped_column()
    faithfulness_score: Mapped[float] = mapped_column()
    groundedness_score: Mapped[float] = mapped_column()
    precision_at_k: Mapped[float] = mapped_column()
    recall_at_k: Mapped[float] = mapped_column()
    config_label: Mapped[str] = mapped_column(String(200))
