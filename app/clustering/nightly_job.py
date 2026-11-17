"""The "evening job" from the design discussion: refreshes embeddings,
refits k-means (real-time assignment centroids) for both candidates and
jobs, re-syncs the Neo4j graph, re-runs Louvain (app/graph/
cluster_entities.py, already built) for both entity types, and links
each run's Louvain community back onto its k-means row -- the explicit
ask: "link Louvain clustering results and k-means once the evening job
is finished." See docs/architecture/data-platform/skill-clustering.md.

Nothing here is reimplemented -- every step calls an existing function
from app/embeddings or app/graph directly. This project has no real
cron/scheduler infra (confirmed: no cron/APScheduler/celery anywhere) --
"nightly" means "the job a real deployment would put on a nightly
schedule," same as every other `docker compose run --rm <job>` batch
script here (golden-cvs, seed, dataquality, ...); wiring an actual
scheduler is a deployment concern, not built in this repo.

Run with: docker compose run --rm cluster-nightly
  [--candidate-k N] [--job-k N] [--embedding-model X]
"""
import argparse
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.clustering.kmeans_skill_clusters import (
    CLUSTER_MODEL_BY_ENTITY_TYPE,
    DEFAULT_K_BY_ENTITY_TYPE,
    fit_skill_clusters,
)
from app.db import SessionLocal
from app.embeddings.compute_embeddings import DEFAULT_EMBEDDING_MODEL, refresh_all
from app.graph import cluster_entities, sync_graph


def _link_louvain_communities(session, entity_type: str, communities: dict[int, list[dict]]) -> int:
    model, id_column = CLUSTER_MODEL_BY_ENTITY_TYPE[entity_type]
    id_attr = getattr(model, id_column)
    now = datetime.now(timezone.utc)
    updated = 0
    for community_id, members in communities.items():
        entity_ids = [m["id"] for m in members]
        if not entity_ids:
            continue
        result = session.execute(
            update(model).where(id_attr.in_(entity_ids))
            .values(louvain_community_id=community_id, graph_synced_at=now)
        )
        updated += result.rowcount
    session.commit()
    return updated


def _agreement_report(session, entity_type: str) -> list[tuple[str, int | None, float]]:
    """For each k-means cluster, the Louvain community its members most
    commonly belong to and what fraction agree -- an honest "do these two
    independent methods see the same structure" check, same framing as
    app/graph/cluster_entities.py's compute_mean_occupation_purity, not
    assumed.
    """
    model, _ = CLUSTER_MODEL_BY_ENTITY_TYPE[entity_type]
    rows = session.execute(select(model.cluster_label, model.louvain_community_id)).all()
    by_cluster: dict[str, list[int | None]] = {}
    for cluster_label, louvain_id in rows:
        by_cluster.setdefault(cluster_label, []).append(louvain_id)

    report = []
    for cluster_label, louvain_ids in sorted(by_cluster.items()):
        counts = Counter(louvain_ids)
        most_common_id, most_common_count = counts.most_common(1)[0]
        report.append((cluster_label, most_common_id, most_common_count / len(louvain_ids)))
    return report


def _run_for_entity_type(session, entity_type: str, embedding_model: str, k: int | None) -> None:
    print(f"-- {entity_type} --")
    fit_result = fit_skill_clusters(session, entity_type, embedding_model=embedding_model, k=k)
    print(f"  k-means: {fit_result}")

    louvain_result = cluster_entities.cluster(entity_type)
    updated = _link_louvain_communities(session, entity_type, louvain_result["communities"])
    print(
        f"  Louvain: {louvain_result['community_count']} communities, "
        f"modularity={louvain_result['modularity']:.4f}, linked {updated} {entity_type} rows"
    )

    print("  Agreement: for each k-means cluster, its most common Louvain community")
    for cluster_label, louvain_id, fraction in _agreement_report(session, entity_type):
        print(f"    {cluster_label}: community {louvain_id} ({fraction:.1%} of members)")


def run(
    embedding_model: str = DEFAULT_EMBEDDING_MODEL, candidate_k: int | None = None, job_k: int | None = None
) -> None:
    session = SessionLocal()
    try:
        print("1/3 Refreshing skills embeddings (candidates + jobs)...")
        embedding_totals = refresh_all(session, embedding_model=embedding_model)
        print(f"    {embedding_totals}")

        print("2/3 Syncing knowledge graph (Postgres -> Neo4j)...")
        sync_counts = sync_graph.sync()
        print(f"    {sync_counts}")

        print("3/3 Refitting k-means + running Louvain + linking back, per entity type:")
        _run_for_entity_type(session, "candidate", embedding_model, candidate_k)
        _run_for_entity_type(session, "job", embedding_model, job_k)
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument(
        "--candidate-k", type=int, default=None, help=f"default: {DEFAULT_K_BY_ENTITY_TYPE['candidate']}"
    )
    parser.add_argument(
        "--job-k", type=int, default=None, help=f"default: {DEFAULT_K_BY_ENTITY_TYPE['job']}"
    )
    args = parser.parse_args()
    run(embedding_model=args.embedding_model, candidate_k=args.candidate_k, job_k=args.job_k)
