"""Candidate/job clustering via Neo4j GDS (Phase 23) — the same native-
Cypher Louvain pattern as Phase 19's app/graph/cluster_skills.py, applied
one level up: instead of clustering skills by shared entities, this
clusters *candidates* (or *jobs*) by shared skills, to surface implicit
"talent segments" / "job families" that aren't just a restatement of the
explicit `occupation_id` label. See docs/knowledge-graph.md "Candidate/job
clustering".

Run with: python -m app.graph.cluster_entities (after sync_graph.py)
"""
from collections import Counter

from app.graph.neo4j_client import get_driver

# Below this, two entities sharing a single incidental skill would be
# treated as "similar" — too weak a signal, and (at 2,000 candidates over
# ~64 domain-anchored skills) a real source of edge-count blowup if left
# unfiltered. Chosen the same way as Phase 16's similarity threshold: swept
# visually against the real graph edge count, not assumed — see
# docs/knowledge-graph.md "Candidate/job clustering".
MIN_SHARED_SKILLS = 2

_ENTITY_CONFIG = {
    "candidate": {
        "label": "Candidate",
        "skill_rel": "HAS_SKILL",
        "similarity_rel": "CANDIDATE_SIMILAR_TO",
        "graph_name": "candidate-similarity-graph",
        "occupation_rel": "TARGETS_OCCUPATION",
    },
    "job": {
        "label": "Job",
        "skill_rel": "REQUIRES_SKILL",
        "similarity_rel": "JOB_SIMILAR_TO",
        "graph_name": "job-similarity-graph",
        "occupation_rel": "POSTED_FOR_OCCUPATION",
    },
}


def _group_by_community(rows) -> dict[int, list[dict]]:
    communities: dict[int, list[dict]] = {}
    for row in rows:
        communities.setdefault(row["communityId"], []).append(
            {"id": row["id"], "occupation_title": row["occupation_title"]}
        )
    return communities


def compute_mean_occupation_purity(communities: dict[int, list[dict]]) -> float:
    """Same methodology as Phase 19's compute_mean_category_purity: for each
    discovered community, the fraction of members sharing that community's
    single most common `occupation_title` — occupation was never used to
    build the skill-similarity graph, so this is a legitimate independent
    check, not a circular one. `None` (candidates with no
    primary_occupation_id, or jobs with no occupation_id) is treated as its
    own bucket, same as any other category value.
    """
    if not communities:
        return 0.0
    purities = []
    for members in communities.values():
        occupation_counts = Counter(m["occupation_title"] for m in members)
        most_common_count = occupation_counts.most_common(1)[0][1]
        purities.append(most_common_count / len(members))
    return sum(purities) / len(purities)


def _derive_similarity_query(config: dict) -> str:
    label, skill_rel, similarity_rel = config["label"], config["skill_rel"], config["similarity_rel"]
    return f"""
        MATCH (e1:{label})-[:{skill_rel}]->(s:Skill)<-[:{skill_rel}]-(e2:{label})
        WHERE e1.id < e2.id
        WITH e1, e2, count(DISTINCT s) AS weight
        WHERE weight >= $min_shared_skills
        MERGE (e1)-[r:{similarity_rel}]->(e2)
        SET r.weight = weight
    """


def cluster(entity_type: str, min_shared_skills: int = MIN_SHARED_SKILLS) -> dict:
    config = _ENTITY_CONFIG[entity_type]
    label, similarity_rel, graph_name = config["label"], config["similarity_rel"], config["graph_name"]

    driver = get_driver()
    try:
        with driver.session() as neo_session:
            neo_session.run(_derive_similarity_query(config), min_shared_skills=min_shared_skills)
            edge_count = neo_session.run(
                f"MATCH ()-[r:{similarity_rel}]->() RETURN count(r) AS n"
            ).single()["n"]

            neo_session.run("CALL gds.graph.drop($graph_name, false)", graph_name=graph_name)
            neo_session.run(
                f"""
                CALL gds.graph.project(
                  $graph_name, '{label}',
                  {{{similarity_rel}: {{orientation: 'UNDIRECTED', properties: 'weight'}}}}
                )
                """,
                graph_name=graph_name,
            )
            louvain_result = neo_session.run(
                """
                CALL gds.louvain.write($graph_name, {
                  writeProperty: 'communityId',
                  relationshipWeightProperty: 'weight'
                })
                YIELD communityCount, modularity
                """,
                graph_name=graph_name,
            ).single()
            rows = list(neo_session.run(
                f"""
                MATCH (e:{label})
                OPTIONAL MATCH (e)-[:{config["occupation_rel"]}]->(o:Occupation)
                RETURN e.communityId AS communityId, e.id AS id, o.title AS occupation_title
                ORDER BY communityId
                """
            ))
            neo_session.run("CALL gds.graph.drop($graph_name, false)", graph_name=graph_name)
    finally:
        driver.close()

    communities = _group_by_community(rows)
    mean_purity = compute_mean_occupation_purity(communities)

    return {
        "entity_type": entity_type,
        "edge_count": edge_count,
        "community_count": louvain_result["communityCount"],
        "modularity": louvain_result["modularity"],
        "communities": communities,
        "mean_occupation_purity": round(mean_purity, 4),
    }


if __name__ == "__main__":
    for entity_type in ("candidate", "job"):
        result = cluster(entity_type)
        print(f"\n=== {entity_type.title()} clustering ===")
        print(f"Similarity edges (>= {MIN_SHARED_SKILLS} shared skills): {result['edge_count']}")
        print(f"Communities found: {result['community_count']}, modularity: {result['modularity']:.4f}")
        print(f"Mean occupation purity (independent check, not used to build the graph): "
              f"{result['mean_occupation_purity']}")
        sizes = sorted(((cid, len(members)) for cid, members in result["communities"].items()),
                        key=lambda x: -x[1])
        for community_id, size in sizes[:10]:
            print(f"  Community {community_id}: {size} members")
