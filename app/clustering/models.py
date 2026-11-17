"""ORM models for skill-based candidate/job clustering: the real-time
k-means assignment path and its link to the (slower, more explainable)
Neo4j Louvain clustering already built in `app/graph/cluster_entities.py`.
See docs/architecture/data-platform/skill-clustering.md.

`skill_cluster_centroids` (now scoped per `entity_type`), `candidate_skill_clusters`,
and `job_skill_clusters` all follow `EntityEmbedding`'s own pattern
(app/embeddings/models.py): one *current* row per key, replaced in place
on each refit/reassignment -- not an append-only history table.
"""
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.embeddings.models import PRODUCTION_VECTOR_DIM


class SkillClusterCentroid(Base):
    __tablename__ = "skill_cluster_centroids"
    __table_args__ = (
        UniqueConstraint(
            "embedding_model", "entity_type", "cluster_label", name="uq_skill_cluster_centroid"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    embedding_model: Mapped[str] = mapped_column()
    # "candidate" | "job" -- one centroid space per entity type, since a
    # candidate's and a job's skill-embedding distributions (and therefore
    # the k that fits each) are measured independently, not assumed equal.
    entity_type: Mapped[str] = mapped_column()
    cluster_label: Mapped[str] = mapped_column()
    centroid: Mapped[list[float]] = mapped_column(Vector(PRODUCTION_VECTOR_DIM))
    updated_at: Mapped[datetime] = mapped_column()


class CandidateSkillCluster(Base):
    __tablename__ = "candidate_skill_clusters"

    # One row per candidate, upserted -- not a composite key with
    # embedding_model, since only one production embedding model is active
    # at a time (same assumption app/embeddings/models.py's own
    # PRODUCTION_VECTOR_DIM comment makes explicit).
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), primary_key=True)
    embedding_model: Mapped[str] = mapped_column()
    cluster_label: Mapped[str] = mapped_column()
    # Null until the nightly job's graph step (sync_graph + Louvain) has run
    # at least once for this candidate -- a real-time k-means assignment
    # (assign_cluster_for_entity) never touches this column.
    louvain_community_id: Mapped[int | None] = mapped_column(nullable=True)
    assigned_at: Mapped[datetime] = mapped_column()
    graph_synced_at: Mapped[datetime | None] = mapped_column(nullable=True)


class JobSkillCluster(Base):
    """Exact mirror of CandidateSkillCluster, one level up (jobs, not
    candidates) -- same real-time k-means + nightly Louvain-link design,
    see app/clustering/kmeans_skill_clusters.py and nightly_job.py."""

    __tablename__ = "job_skill_clusters"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    embedding_model: Mapped[str] = mapped_column()
    cluster_label: Mapped[str] = mapped_column()
    louvain_community_id: Mapped[int | None] = mapped_column(nullable=True)
    assigned_at: Mapped[datetime] = mapped_column()
    graph_synced_at: Mapped[datetime | None] = mapped_column(nullable=True)
