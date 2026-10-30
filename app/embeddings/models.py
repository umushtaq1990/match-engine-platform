"""ORM models for Phase 17's embedding storage.

`entity_embeddings` is the **production** table: pgvector-indexed (HNSW,
added by hand in the migration — see its own comment), one active model per
dimension at a time. `embedding_eval_results` is the **offline comparison**
table: plain JSONB, no index, used to compare alternate models/configs
against the golden dataset before anything is promoted to production. This
split exists specifically to resolve pgvector's fixed-vector-width
constraint cleanly — see docs/embedding-matching.md.
"""
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Fixed to the active v1 production model (all-minilm, 384-dim — confirmed
# by direct measurement against the running ollama service, see
# docs/embedding-matching.md). Promoting a wider model requires a new
# migration and a full re-embed batch, by design, not a silent hot-swap.
PRODUCTION_VECTOR_DIM = 384


class EntityEmbedding(Base):
    __tablename__ = "entity_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "dimension", "embedding_model",
            name="uq_entity_embedding",
        ),
        # Declared here (matching the hand-written CREATE INDEX in the
        # migration — autogenerate can't emit pgvector's HNSW access method
        # itself) purely so `alembic check` doesn't perpetually flag it as a
        # missing op; this does not re-create it.
        Index(
            "ix_entity_embeddings_ann", "vector",
            postgresql_using="hnsw", postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[int] = mapped_column(index=True)
    dimension: Mapped[str] = mapped_column(String(20))
    embedding_model: Mapped[str] = mapped_column(String(100))
    # Hash of whatever structured/text data produced this vector — a
    # mismatch on recheck means the row is stale. See app/embeddings/hashing.py.
    source_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list[float]] = mapped_column(Vector(PRODUCTION_VECTOR_DIM))
    computed_at: Mapped[datetime] = mapped_column()


class EmbeddingEvalResult(Base):
    __tablename__ = "embedding_eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column()
    config_label: Mapped[str] = mapped_column(String(200))
    embedding_model: Mapped[str] = mapped_column(String(100))
    # 'rule_based' | 'blended' | 'embeddings_only'
    scoring_mode: Mapped[str] = mapped_column(String(20))
    sample_size: Mapped[int] = mapped_column()
    precision_at_k_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    recall_at_k_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    ndcg_at_k_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    mrr_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    # Accuracy on the hand-labeled near-miss pairs — null for configs that
    # don't produce a similarity score (not applicable to rule_based mode).
    near_miss_accuracy: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
