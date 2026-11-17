"""k-means over candidates'/jobs' "skills" embeddings (Phase 17's
`entity_embeddings`) -- the real-time half of skill-based clustering. See
docs/architecture/data-platform/skill-clustering.md for why this, and not
the Neo4j Louvain clustering in app/graph/cluster_entities.py, is what a
new signup/job posting or a profile/posting edit gets assigned against:
nearest-centroid is O(k), cheap enough to run inline on a request;
Louvain is a global algorithm over the whole graph, refit only
periodically (see app/clustering/nightly_job.py).

No StandardScaler, deliberately -- unlike app/golden/drilldown/data.py's
`compute_behavioral_clusters` (raw KPIs on wildly different scales), these
vectors are already a trained model's (all-minilm) semantic embedding
space; standardizing per-dimension would distort the geometry the model
itself produced.

Entity-type-aware ("candidate" | "job") rather than two parallel modules
-- candidates and jobs differ only in which table/id column holds their
cluster assignment (CandidateSkillCluster/candidate_id vs.
JobSkillCluster/job_id), dispatched via CLUSTER_MODEL_BY_ENTITY_TYPE
below; the fit/assign logic itself is identical.
"""
from datetime import datetime, timezone

import numpy as np
from sklearn.cluster import KMeans
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.clustering.models import CandidateSkillCluster, JobSkillCluster, SkillClusterCentroid
from app.embeddings.compute_embeddings import DEFAULT_EMBEDDING_MODEL, refresh_entity
from app.embeddings.models import EntityEmbedding

# entity_type -> (ORM model, its primary-key/id column name).
CLUSTER_MODEL_BY_ENTITY_TYPE = {
    "candidate": (CandidateSkillCluster, "candidate_id"),
    "job": (JobSkillCluster, "job_id"),
}

# Measured, not assumed -- an earlier default of 4 for candidates (to
# match the known domain count) was checked against a real elbow/
# silhouette sweep (app/golden/drilldown/data.py's compute_k_diagnostics,
# same vectors) and didn't hold up: silhouette is low and fairly flat
# across k (weak global separation, consistent with knowledge-graph.md's
# own candidate/job purity findings), but with a real local peak around
# k=7-8, not k=4. That peak also lines up with Louvain's own,
# independently discovered community count on this same candidate
# population (knowledge-graph.md "Candidate/job clustering": 8
# communities) -- two independent methods landing near the same k is a
# real signal, not coincidence. 8 chosen over 7 for that direct agreement
# with Louvain. Jobs' own sweep (over all 4,002 jobs' real skills
# embeddings) found silhouette peaking at k=8 (0.2422) too, close to
# (not identical to) Louvain's independently-found 7 job communities
# (modularity 0.6879, measured the same session) -- 8 taken as the
# measured best here since it's the sweep's own global optimum, not
# forced to match Louvain the way candidates' happened to.
DEFAULT_K_BY_ENTITY_TYPE = {"candidate": 8, "job": 8}


def _cluster_label(k_index: int) -> str:
    return f"skill_cluster_{k_index}"


def fit_skill_clusters(
    session: Session, entity_type: str, embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    k: int | None = None,
) -> dict:
    """Refits k-means over every entity's (of `entity_type`) current
    skills embedding, replaces that (embedding_model, entity_type)'s
    centroids, and reassigns every one of those entities. Does not touch
    louvain_community_id/graph_synced_at -- that's exclusively the graph
    step's job (app/clustering/nightly_job.py) so a k-means-only refit
    never clobbers the last Louvain sync.
    """
    k = k if k is not None else DEFAULT_K_BY_ENTITY_TYPE[entity_type]
    model, id_column = CLUSTER_MODEL_BY_ENTITY_TYPE[entity_type]

    rows = session.execute(
        select(EntityEmbedding.entity_id, EntityEmbedding.vector).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.dimension == "skills",
            EntityEmbedding.embedding_model == embedding_model,
        )
    ).all()
    if len(rows) < k:
        raise ValueError(
            f"Only {len(rows)} {entity_type}s have a skills embedding -- need at least k={k} to fit."
        )

    entity_ids = [r.entity_id for r in rows]
    vectors = np.array([r.vector for r in rows], dtype=np.float64)

    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10).fit(vectors)
    labels = kmeans.predict(vectors)
    now = datetime.now(timezone.utc)

    session.execute(
        delete(SkillClusterCentroid).where(
            SkillClusterCentroid.embedding_model == embedding_model,
            SkillClusterCentroid.entity_type == entity_type,
        )
    )
    for i, centroid in enumerate(kmeans.cluster_centers_):
        session.add(SkillClusterCentroid(
            embedding_model=embedding_model, entity_type=entity_type, cluster_label=_cluster_label(i),
            centroid=centroid.tolist(), updated_at=now,
        ))
    session.flush()

    for entity_id, label in zip(entity_ids, labels):
        _upsert_cluster(
            session, model, id_column, entity_id, embedding_model, _cluster_label(int(label)), now
        )
    session.commit()

    counts = {int(label): int((labels == label).sum()) for label in set(labels)}
    return {"k": k, "entity_type": entity_type, "entity_count": len(entity_ids), "cluster_sizes": counts}


def _upsert_cluster(
    session: Session, model, id_column: str, entity_id: int, embedding_model: str,
    cluster_label: str, assigned_at: datetime,
) -> None:
    stmt = insert(model).values(
        **{id_column: entity_id}, embedding_model=embedding_model,
        cluster_label=cluster_label, assigned_at=assigned_at,
    ).on_conflict_do_update(
        index_elements=[id_column],
        set_={"embedding_model": embedding_model, "cluster_label": cluster_label, "assigned_at": assigned_at},
    )
    session.execute(stmt)


def assign_cluster_for_entity(
    session: Session, entity_type: str, entity_id: int, embedding_model: str = DEFAULT_EMBEDDING_MODEL
) -> str:
    """The real-time hot path: a new signup/job posting or a profile/
    posting edit. Ensures the entity's skills embedding reflects its
    *current* skills (reusing app/embeddings/compute_embeddings.
    refresh_entity -- already hash-invalidated, so this is a no-op if
    nothing actually changed), then returns the nearest persisted
    centroid. Never touches louvain_community_id -- that stays whatever
    the last nightly graph sync set it to, until the next one runs.
    """
    model, id_column = CLUSTER_MODEL_BY_ENTITY_TYPE[entity_type]
    refresh_entity(session, entity_type, entity_id, embedding_model)

    vector_row = session.execute(
        select(EntityEmbedding.vector).where(
            EntityEmbedding.entity_type == entity_type,
            EntityEmbedding.entity_id == entity_id,
            EntityEmbedding.dimension == "skills",
            EntityEmbedding.embedding_model == embedding_model,
        )
    ).scalar_one_or_none()
    if vector_row is None:
        raise ValueError(f"{entity_type} {entity_id} has no skills embedding -- nothing to assign against.")

    centroids = session.execute(
        select(SkillClusterCentroid.cluster_label, SkillClusterCentroid.centroid).where(
            SkillClusterCentroid.embedding_model == embedding_model,
            SkillClusterCentroid.entity_type == entity_type,
        )
    ).all()
    if not centroids:
        raise ValueError(
            f"No fitted {entity_type} centroids for embedding_model={embedding_model} -- "
            "run fit_skill_clusters first."
        )

    vector = np.array(vector_row, dtype=np.float64)
    centroid_matrix = np.array([c.centroid for c in centroids], dtype=np.float64)
    distances = np.linalg.norm(centroid_matrix - vector, axis=1)
    nearest_label = centroids[int(np.argmin(distances))].cluster_label

    now = datetime.now(timezone.utc)
    _upsert_cluster(session, model, id_column, entity_id, embedding_model, nearest_label, now)
    session.commit()
    return nearest_label
