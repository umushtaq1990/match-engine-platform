"""The "evening job" from the design discussion: refreshes embeddings,
refits k-means (real-time assignment centroids), re-syncs the Neo4j graph,
re-runs Louvain (app/graph/cluster_entities.py, already built), and links
that run's Louvain community back onto each candidate's k-means row --
the explicit ask: "link Louvain clustering results and k-means once the
evening job is finished." See docs/architecture/data-platform/
skill-clustering.md.

Nothing here is reimplemented -- every step calls an existing function
from app/embeddings or app/graph directly. This project has no real
cron/scheduler infra (confirmed: no cron/APScheduler/celery anywhere) --
"nightly" means "the job a real deployment would put on a nightly
schedule," same as every other `docker compose run --rm <job>` batch
script here (golden-cvs, seed, dataquality, ...); wiring an actual
scheduler is a deployment concern, not built in this repo.

Run with: docker compose run --rm cluster-nightly [--k N] [--embedding-model X]
"""
import argparse
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.clustering.kmeans_skill_clusters import DEFAULT_K, fit_skill_clusters
from app.clustering.models import CandidateSkillCluster
from app.db import SessionLocal
from app.embeddings.compute_embeddings import DEFAULT_EMBEDDING_MODEL, refresh_all
from app.graph import cluster_entities, sync_graph


def _link_louvain_communities(session, communities: dict[int, list[dict]]) -> int:
    now = datetime.now(timezone.utc)
    updated = 0
    for community_id, members in communities.items():
        candidate_ids = [m["id"] for m in members]
        if not candidate_ids:
            continue
        result = session.execute(
            update(CandidateSkillCluster)
            .where(CandidateSkillCluster.candidate_id.in_(candidate_ids))
            .values(louvain_community_id=community_id, graph_synced_at=now)
        )
        updated += result.rowcount
    session.commit()
    return updated


def _agreement_report(session) -> list[tuple[str, int | None, float]]:
    """For each k-means cluster, the Louvain community its members most
    commonly belong to and what fraction agree -- an honest "do these two
    independent methods see the same structure" check, same framing as
    app/graph/cluster_entities.py's compute_mean_occupation_purity, not
    assumed.
    """
    rows = session.execute(
        select(CandidateSkillCluster.cluster_label, CandidateSkillCluster.louvain_community_id)
    ).all()
    by_cluster: dict[str, list[int | None]] = {}
    for cluster_label, louvain_id in rows:
        by_cluster.setdefault(cluster_label, []).append(louvain_id)

    report = []
    for cluster_label, louvain_ids in sorted(by_cluster.items()):
        counts = Counter(louvain_ids)
        most_common_id, most_common_count = counts.most_common(1)[0]
        report.append((cluster_label, most_common_id, most_common_count / len(louvain_ids)))
    return report


def run(embedding_model: str = DEFAULT_EMBEDDING_MODEL, k: int = DEFAULT_K) -> None:
    session = SessionLocal()
    try:
        print("1/4 Refreshing candidate skills embeddings...")
        embedding_totals = refresh_all(session, embedding_model=embedding_model)
        print(f"    {embedding_totals}")

        print("2/4 Refitting k-means skill clusters...")
        fit_result = fit_skill_clusters(session, embedding_model=embedding_model, k=k)
        print(f"    {fit_result}")

        print("3/4 Syncing knowledge graph (Postgres -> Neo4j)...")
        sync_counts = sync_graph.sync()
        print(f"    {sync_counts}")

        print("4/4 Running Louvain candidate clustering + linking back to k-means...")
        louvain_result = cluster_entities.cluster("candidate")
        updated = _link_louvain_communities(session, louvain_result["communities"])
        print(
            f"    {louvain_result['community_count']} Louvain communities, "
            f"modularity={louvain_result['modularity']:.4f}, linked {updated} candidate rows"
        )

        print("\nAgreement: for each k-means cluster, its most common Louvain community")
        for cluster_label, louvain_id, fraction in _agreement_report(session):
            print(f"  {cluster_label}: community {louvain_id} ({fraction:.1%} of members)")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    args = parser.parse_args()
    run(embedding_model=args.embedding_model, k=args.k)
