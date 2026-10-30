"""Skill co-occurrence clustering via Neo4j GDS, running natively in Cypher
(Phase 19) — no Python graph library involved; the algorithm itself runs
inside the database engine. See docs/knowledge-graph.md "Clustering".

Run with: python -m app.graph.cluster_skills (after sync_graph.py)
"""
from collections import Counter

from app.graph.neo4j_client import get_driver

GRAPH_NAME = "skill-graph"

# Two skills co-occur if the same entity (candidate/job/occupation/course)
# has an outgoing skill-relationship to both — spans HAS_SKILL/
# REQUIRES_SKILL/SKILL_OF_OCCUPATION/TEACHES_SKILL at once since `entity` is
# untyped and the relationship type is unconstrained.
_DERIVE_COOCCURRENCE = """
MATCH (entity)-->(s1:Skill)
MATCH (entity)-->(s2:Skill)
WHERE s1.id < s2.id
WITH s1, s2, count(DISTINCT entity) AS weight
MERGE (s1)-[r:CO_OCCURS_WITH]->(s2)
SET r.weight = weight
"""

_PROJECT_GRAPH = """
CALL gds.graph.project(
  $graph_name, 'Skill',
  {CO_OCCURS_WITH: {orientation: 'UNDIRECTED', properties: 'weight'}}
)
"""

_RUN_LOUVAIN = """
CALL gds.louvain.write($graph_name, {
  writeProperty: 'communityId',
  relationshipWeightProperty: 'weight'
})
YIELD communityCount, modularity
"""

_FETCH_RESULTS = """
MATCH (s:Skill)
RETURN s.communityId AS communityId, s.name AS name, s.category AS category
ORDER BY communityId
"""


def _group_by_community(rows) -> dict[int, list[dict]]:
    communities: dict[int, list[dict]] = {}
    for row in rows:
        communities.setdefault(row["communityId"], []).append(
            {"name": row["name"], "category": row["category"]}
        )
    return communities


def compute_mean_category_purity(communities: dict[int, list[dict]]) -> float:
    """For each discovered community, what fraction of its members share
    that community's single most common known category — a category never
    used to build the co-occurrence graph itself (see
    docs/knowledge-graph.md "Clustering"), so this is a legitimate,
    independent check, not a circular one. Pure function, no DB — unit
    tested directly in tests/graph/test_cluster_skills.py.
    """
    if not communities:
        return 0.0
    purities = []
    for members in communities.values():
        category_counts = Counter(m["category"] for m in members)
        most_common_count = category_counts.most_common(1)[0][1]
        purities.append(most_common_count / len(members))
    return sum(purities) / len(purities)


def cluster() -> dict:
    driver = get_driver()
    try:
        with driver.session() as neo_session:
            neo_session.run(_DERIVE_COOCCURRENCE)
            neo_session.run("CALL gds.graph.drop($graph_name, false)", graph_name=GRAPH_NAME)
            neo_session.run(_PROJECT_GRAPH, graph_name=GRAPH_NAME)
            louvain_result = neo_session.run(_RUN_LOUVAIN, graph_name=GRAPH_NAME).single()
            rows = list(neo_session.run(_FETCH_RESULTS))
            neo_session.run("CALL gds.graph.drop($graph_name, false)", graph_name=GRAPH_NAME)
    finally:
        driver.close()

    communities = _group_by_community(rows)
    mean_purity = compute_mean_category_purity(communities)

    return {
        "community_count": louvain_result["communityCount"],
        "modularity": louvain_result["modularity"],
        "communities": communities,
        "mean_category_purity": round(mean_purity, 4),
    }


if __name__ == "__main__":
    result = cluster()
    print(f"Communities found: {result['community_count']}, modularity: {result['modularity']:.4f}")
    print(f"Mean category purity (independent check, not used to build the graph): "
          f"{result['mean_category_purity']}")
    for community_id, members in sorted(result["communities"].items()):
        names = ", ".join(m["name"] for m in members)
        categories = Counter(m["category"] for m in members)
        print(f"  Community {community_id} ({len(members)} skills, categories={dict(categories)}): {names}")
