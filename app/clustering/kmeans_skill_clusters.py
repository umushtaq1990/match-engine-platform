"""k-means over candidates' "skills" embeddings (Phase 17's
`entity_embeddings`) -- the real-time half of skill-based clustering. See
docs/architecture/data-platform/skill-clustering.md for why this, and not
the Neo4j Louvain clustering in app/graph/cluster_entities.py, is what a
new signup or a profile edit gets assigned against: nearest-centroid is
O(k), cheap enough to run inline on a request; Louvain is a global
algorithm over the whole graph, refit only periodically (see
app/clustering/nightly_job.py).

No StandardScaler, deliberately -- unlike app/golden/drilldown/data.py's
`compute_behavioral_clusters` (raw KPIs on wildly different scales), these
vectors are already a trained model's (all-minilm) semantic embedding
space; standardizing per-dimension would distort the geometry the model
itself produced.
"""
from datetime import datetime, timezone

import numpy as np
from sklearn.cluster import KMeans
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.clustering.models import CandidateSkillCluster, SkillClusterCentroid
from app.embeddings.compute_embeddings import DEFAULT_EMBEDDING_MODEL, refresh_entity
from app.embeddings.models import EntityEmbedding

# Matches the 4 real domains the candidate graph already converges to
# (docs/architecture/data-platform/knowledge-graph.md "Candidate/job
# clustering") -- chosen so this unsupervised fit can be checked against
# known structure, same "does it rediscover what we know" framing as
# app/golden/drilldown/data.py's behavioral k=5 (matched to persona count).
DEFAULT_K = 4


def _cluster_label(k_index: int) -> str:
    return f"skill_cluster_{k_index}"


def fit_skill_clusters(
    session: Session, embedding_model: str = DEFAULT_EMBEDDING_MODEL, k: int = DEFAULT_K
) -> dict:
    """Refits k-means over every candidate's current skills embedding,
    replaces this model's centroids, and reassigns every one of those
    candidates. Does not touch louvain_community_id/graph_synced_at --
    that's exclusively the graph step's job (app/clustering/nightly_job.py)
    so a k-means-only refit never clobbers the last Louvain sync.
    """
    rows = session.execute(
        select(EntityEmbedding.entity_id, EntityEmbedding.vector).where(
            EntityEmbedding.entity_type == "candidate",
            EntityEmbedding.dimension == "skills",
            EntityEmbedding.embedding_model == embedding_model,
        )
    ).all()
    if len(rows) < k:
        raise ValueError(
            f"Only {len(rows)} candidates have a skills embedding -- need at least k={k} to fit."
        )

    candidate_ids = [r.entity_id for r in rows]
    vectors = np.array([r.vector for r in rows], dtype=np.float64)

    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10).fit(vectors)
    labels = kmeans.predict(vectors)
    now = datetime.now(timezone.utc)

    session.execute(
        delete(SkillClusterCentroid).where(SkillClusterCentroid.embedding_model == embedding_model)
    )
    for i, centroid in enumerate(kmeans.cluster_centers_):
        session.add(SkillClusterCentroid(
            embedding_model=embedding_model, cluster_label=_cluster_label(i),
            centroid=centroid.tolist(), updated_at=now,
        ))
    session.flush()

    for candidate_id, label in zip(candidate_ids, labels):
        _upsert_candidate_cluster(session, candidate_id, embedding_model, _cluster_label(int(label)), now)
    session.commit()

    counts = {int(label): int((labels == label).sum()) for label in set(labels)}
    return {"k": k, "candidate_count": len(candidate_ids), "cluster_sizes": counts}


def _upsert_candidate_cluster(
    session: Session, candidate_id: int, embedding_model: str, cluster_label: str, assigned_at: datetime
) -> None:
    stmt = insert(CandidateSkillCluster).values(
        candidate_id=candidate_id, embedding_model=embedding_model,
        cluster_label=cluster_label, assigned_at=assigned_at,
    ).on_conflict_do_update(
        index_elements=["candidate_id"],
        set_={"embedding_model": embedding_model, "cluster_label": cluster_label, "assigned_at": assigned_at},
    )
    session.execute(stmt)


def assign_cluster_for_candidate(
    session: Session, candidate_id: int, embedding_model: str = DEFAULT_EMBEDDING_MODEL
) -> str:
    """The real-time hot path: a new signup or a profile edit. Ensures the
    candidate's skills embedding reflects their *current* skills (reusing
    app/embeddings/compute_embeddings.refresh_entity -- already
    hash-invalidated, so this is a no-op if nothing actually changed), then
    returns the nearest persisted centroid. Never touches
    louvain_community_id -- that stays whatever the last nightly graph sync
    set it to, until the next one runs.
    """
    refresh_entity(session, "candidate", candidate_id, embedding_model)

    vector_row = session.execute(
        select(EntityEmbedding.vector).where(
            EntityEmbedding.entity_type == "candidate",
            EntityEmbedding.entity_id == candidate_id,
            EntityEmbedding.dimension == "skills",
            EntityEmbedding.embedding_model == embedding_model,
        )
    ).scalar_one_or_none()
    if vector_row is None:
        raise ValueError(f"Candidate {candidate_id} has no skills embedding -- nothing to assign against.")

    centroids = session.execute(
        select(SkillClusterCentroid.cluster_label, SkillClusterCentroid.centroid).where(
            SkillClusterCentroid.embedding_model == embedding_model
        )
    ).all()
    if not centroids:
        raise ValueError(
            f"No fitted centroids for embedding_model={embedding_model} -- run fit_skill_clusters first."
        )

    vector = np.array(vector_row, dtype=np.float64)
    centroid_matrix = np.array([c.centroid for c in centroids], dtype=np.float64)
    distances = np.linalg.norm(centroid_matrix - vector, axis=1)
    nearest_label = centroids[int(np.argmin(distances))].cluster_label

    now = datetime.now(timezone.utc)
    _upsert_candidate_cluster(session, candidate_id, embedding_model, nearest_label, now)
    session.commit()
    return nearest_label
