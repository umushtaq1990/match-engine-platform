"""Graph-based explainability for the Candidate/Job Profile 360 pages --
deliberately NOT a recommendation signal. Graph-based collaborative
filtering was investigated as a possible ranking signal and found not
viable on this project's current synthetic engagement data (see
docs/architecture/data-platform/knowledge-graph.md's Verification &
Results for the full leave-one-out investigation). This module answers a
different question instead: given a candidate or job, what does their
actual position in the knowledge graph look like -- which peers, jobs,
and courses are they adjacent to via shared skills -- rendered as a
network diagram plus a plain-language narrative, both strictly grounded
in real graph facts, to help a human reviewer understand structure, not
to rank anything.

Every Cypher query here ranks by shared-skill count and LIMITs before
fetching exact edges, the same bounded-fan-out shape proven necessary by
the earlier graph-CF benchmark (an unbounded multi-hop walk through
common skills risks multi-second queries on high-degree nodes).

LLM narrative generation reuses app.extraction.ollama_client.generate()
directly, not app.extraction.pipeline (which pulls in skill_cache and a
much larger import chain for one constant) -- the model name is
duplicated as a literal below, same discipline as this package's
existing _SKILLS_EMBEDDING_MODEL constant in data.py.

Narratives are cached in Postgres (app.golden.models.GraphNarrative), not
just in-process -- durable across restarts, shared across replicas, and
regenerated only when the underlying facts actually change (a content
hash, not a time-based guess). See get_or_generate_graph_narrative.
"""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.ollama_client import generate
from app.golden.models import GraphNarrative
from app.graph.neo4j_client import get_driver

# Mirrors app/extraction/pipeline.py's DEFAULT_LLM_MODEL -- duplicated as a
# literal rather than imported so this app doesn't pull in that module's
# mlflow-tracing-pipeline/skill_cache dependency chain for one string.
_NARRATIVE_LLM_MODEL = "qwen2.5:1.5b"

MAX_PEERS = 8
MAX_JOBS = 8
MAX_COURSES = 5


def _top_by_shared_skill(neo_session, query: str, center_id: int, limit: int) -> list[dict]:
    return neo_session.run(query, id=center_id, limit=limit).data()


def _edges_for(neo_session, query: str, center_id: int, target_ids: list[int]) -> list[dict]:
    if not target_ids:
        return []
    return neo_session.run(query, id=center_id, target_ids=target_ids).data()


_CANDIDATE_SKILLS = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill) RETURN sk.id AS id, sk.name AS name
"""

_CANDIDATE_TOP_JOBS = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:REQUIRES_SKILL]-(j:Job)
WITH j, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN j.id AS id, j.title AS label, shared
"""
_CANDIDATE_TOP_PEERS = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:HAS_SKILL]-(p:Candidate)
WHERE p.id <> $id
WITH p, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN p.id AS id, p.full_name AS label, shared
"""
_CANDIDATE_TOP_COURSES = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:TEACHES_SKILL]-(co:Course)
WITH co, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN co.id AS id, co.title AS label, shared
"""
_CANDIDATE_JOB_EDGES = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:REQUIRES_SKILL]-(j:Job)
WHERE j.id IN $target_ids
RETURN j.id AS target, sk.id AS skill_id
"""
_CANDIDATE_PEER_EDGES = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:HAS_SKILL]-(p:Candidate)
WHERE p.id IN $target_ids
RETURN p.id AS target, sk.id AS skill_id
"""
_CANDIDATE_COURSE_EDGES = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:TEACHES_SKILL]-(co:Course)
WHERE co.id IN $target_ids
RETURN co.id AS target, sk.id AS skill_id
"""

_JOB_SKILLS = "MATCH (j:Job {id: $id})-[:REQUIRES_SKILL]->(sk:Skill) RETURN sk.id AS id, sk.name AS name"

_JOB_TOP_CANDIDATES = """
MATCH (j:Job {id: $id})-[:REQUIRES_SKILL]->(sk:Skill)<-[:HAS_SKILL]-(c:Candidate)
WITH c, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN c.id AS id, c.full_name AS label, shared
"""
_JOB_TOP_JOBS = """
MATCH (j:Job {id: $id})-[:REQUIRES_SKILL]->(sk:Skill)<-[:REQUIRES_SKILL]-(other:Job)
WHERE other.id <> $id
WITH other, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN other.id AS id, other.title AS label, shared
"""
_JOB_TOP_COURSES = """
MATCH (j:Job {id: $id})-[:REQUIRES_SKILL]->(sk:Skill)<-[:TEACHES_SKILL]-(co:Course)
WITH co, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN co.id AS id, co.title AS label, shared
"""
_JOB_CANDIDATE_EDGES = """
MATCH (j:Job {id: $id})-[:REQUIRES_SKILL]->(sk:Skill)<-[:HAS_SKILL]-(c:Candidate)
WHERE c.id IN $target_ids
RETURN c.id AS target, sk.id AS skill_id
"""
_JOB_JOB_EDGES = """
MATCH (j:Job {id: $id})-[:REQUIRES_SKILL]->(sk:Skill)<-[:REQUIRES_SKILL]-(other:Job)
WHERE other.id IN $target_ids
RETURN other.id AS target, sk.id AS skill_id
"""
_JOB_COURSE_EDGES = """
MATCH (j:Job {id: $id})-[:REQUIRES_SKILL]->(sk:Skill)<-[:TEACHES_SKILL]-(co:Course)
WHERE co.id IN $target_ids
RETURN co.id AS target, sk.id AS skill_id
"""


def _build_ego_network(
    center_id: int, center_type: str, center_label: str,
    skills_query: str, top_queries: dict[str, str], edge_queries: dict[str, str],
    max_peers: int, max_jobs: int, max_courses: int,
    similar_df=None, similar_id_col: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Shared traversal shape for both entity types -- only the Cypher
    strings and which neighbor type plays "peer" (Candidate vs. Job)
    differ between build_candidate_ego_network/build_job_ego_network.
    """
    limits = {"peers": max_peers, "jobs": max_jobs, "courses": max_courses}
    similarity_by_id: dict[int, float] = {}
    if similar_df is not None and not similar_df.empty and similar_id_col:
        for row in similar_df.itertuples():
            distance = getattr(row, "distance")
            similarity_by_id[getattr(row, similar_id_col)] = 1.0 / (1.0 + float(distance))

    with get_driver() as driver, driver.session() as neo_session:
        skill_rows = neo_session.run(skills_query, id=center_id).data()
        top_hits = {
            kind: _top_by_shared_skill(neo_session, query, center_id, limits[kind])
            for kind, query in top_queries.items()
        }
        edge_rows = {
            kind: _edges_for(
                neo_session, edge_queries[kind], center_id,
                [hit["id"] for hit in top_hits[kind]],
            )
            for kind in top_queries
        }

    nodes: list[dict] = [{
        "node_id": f"{center_type}:{center_id}", "type": center_type,
        "label": center_label, "shared_skill_count": None,
        "embedding_similarity": None, "is_center": True,
    }]
    edges: list[dict] = []

    skill_node_id = {}
    for sk in skill_rows:
        node_id = f"Skill:{sk['id']}"
        skill_node_id[sk["id"]] = node_id
        nodes.append({
            "node_id": node_id, "type": "Skill", "label": sk["name"],
            "shared_skill_count": None, "embedding_similarity": None, "is_center": False,
        })
        edges.append({
            "source": f"{center_type}:{center_id}", "target": node_id, "weight": 1,
        })

    # "peers" always means Candidate nodes (a candidate center's own peers,
    # or a job center's matching candidates); "jobs" always means Job nodes
    # (a candidate center's adjacent postings, or a job center's competing
    # postings); "courses" always means Course nodes. Which Cypher query
    # each bucket actually runs (see build_candidate_ego_network/
    # build_job_ego_network) is what varies, not this type mapping.
    kind_to_type = {"peers": "Candidate", "jobs": "Job", "courses": "Course"}
    for kind, hits in top_hits.items():
        node_type = kind_to_type[kind]
        for hit in hits:
            node_id = f"{node_type}:{hit['id']}"
            nodes.append({
                "node_id": node_id, "type": node_type, "label": hit["label"] or f"#{hit['id']}",
                "shared_skill_count": hit["shared"],
                "embedding_similarity": similarity_by_id.get(hit["id"]),
                "is_center": False,
            })
        for edge in edge_rows[kind]:
            source_id = skill_node_id.get(edge["skill_id"])
            target_id = f"{node_type}:{edge['target']}"
            if source_id is None:
                continue
            edges.append({"source": source_id, "target": target_id, "weight": 1})

    return nodes, edges


def build_candidate_ego_network(
    candidate_id: int, candidate_label: str,
    max_peers: int = MAX_PEERS, max_jobs: int = MAX_JOBS, max_courses: int = MAX_COURSES,
    similar_df=None,
) -> tuple[list[dict], list[dict]]:
    """2-hop ego-network for a candidate: their skills, plus the jobs,
    peer candidates, and courses adjacent to those skills, ranked and
    capped by shared-skill count. `similar_df` is the already-loaded
    `find_similar_entities(..., "candidate_id", ...)` result from
    data.py (not re-queried) -- used only to cross-reference an
    embedding-similarity score onto any peer that also appears there.

    Returns:
        tuple[list[dict], list[dict]]: `(nodes, edges)` -- see
            graph_chart.render_ego_network for the shape each node/edge
            dict carries.
    """
    return _build_ego_network(
        candidate_id, "Candidate", candidate_label,
        _CANDIDATE_SKILLS,
        {"jobs": _CANDIDATE_TOP_JOBS, "peers": _CANDIDATE_TOP_PEERS, "courses": _CANDIDATE_TOP_COURSES},
        {"jobs": _CANDIDATE_JOB_EDGES, "peers": _CANDIDATE_PEER_EDGES, "courses": _CANDIDATE_COURSE_EDGES},
        max_peers, max_jobs, max_courses,
        similar_df=similar_df, similar_id_col="candidate_id",
    )


def build_job_ego_network(
    job_id: int, job_label: str,
    max_peers: int = MAX_PEERS, max_jobs: int = MAX_JOBS, max_courses: int = MAX_COURSES,
    similar_df=None,
) -> tuple[list[dict], list[dict]]:
    """Job-side mirror of build_candidate_ego_network: the job's required
    skills, plus the candidates, other jobs, and courses adjacent to
    those skills. `similar_df` is the page's existing
    `find_similar_entities(..., "job_id", ...)` result, cross-referenced
    onto adjacent jobs the same way the candidate version does for peers.

    Returns:
        tuple[list[dict], list[dict]]: `(nodes, edges)`, same shape as
            build_candidate_ego_network.
    """
    return _build_ego_network(
        job_id, "Job", job_label,
        _JOB_SKILLS,
        {"peers": _JOB_TOP_CANDIDATES, "jobs": _JOB_TOP_JOBS, "courses": _JOB_TOP_COURSES},
        {"peers": _JOB_CANDIDATE_EDGES, "jobs": _JOB_JOB_EDGES, "courses": _JOB_COURSE_EDGES},
        max_peers, max_jobs, max_courses,
        similar_df=similar_df, similar_id_col="job_id",
    )


def attach_candidate_segments(nodes: list[dict], segment_by_candidate_id: dict[int, str]) -> None:
    """Mutates `nodes` in place, adding a `segment` key (one of
    `app.analytics.segmentation`'s `dormant`/`active_seeker`/
    `passive_seeker`, or `None`) to every `Candidate`-type node --
    including the center if it's a candidate. graph_chart.py renders this
    as a colored node outline, a second signal independent of the type
    color. Non-Candidate nodes are left untouched.

    Args:
        nodes: a build_candidate_ego_network/build_job_ego_network result
            (mutated in place, not returned).
        segment_by_candidate_id: `{candidate_id: segment}`, from the
            page's already-loaded behavioral data
            (`app.golden.drilldown.data.load_behavioral_rows`) -- not
            re-queried here.
    """
    for node in nodes:
        if node["type"] != "Candidate":
            continue
        candidate_id = int(node["node_id"].split(":", 1)[1])
        node["segment"] = segment_by_candidate_id.get(candidate_id)


def summarize_graph_facts(nodes: list[dict], entity_label: str) -> dict:
    """Plain structured facts about an ego-network, no LLM involved --
    the grounding `generate_graph_narrative` is restricted to, and also
    directly displayable as a facts panel. Deliberately doesn't repeat
    the Louvain-community metric already shown elsewhere on the profile
    page; this is the new information the ego-network query adds.

    Returns:
        dict: `entity_label`, `skill_count`, and per neighbor type
            (`peers`/`jobs`/`courses`) a `count` plus a `top` list of
            `{label, shared_skill_count, embedding_similarity}` for the
            3 strongest connections.
    """
    facts: dict = {"entity_label": entity_label, "skill_count": 0, "peers": {}, "jobs": {}, "courses": {}}
    by_type: dict[str, list[dict]] = {"Skill": [], "Candidate": [], "Job": [], "Course": []}
    for node in nodes:
        if node["is_center"]:
            continue
        by_type.setdefault(node["type"], []).append(node)

    facts["skill_count"] = len(by_type.get("Skill", []))
    type_to_kind = {"Candidate": "peers", "Job": "jobs", "Course": "courses"}
    for node_type, kind in type_to_kind.items():
        entries = sorted(by_type.get(node_type, []), key=lambda n: n["shared_skill_count"] or 0, reverse=True)
        facts[kind] = {
            "count": len(entries),
            "top": [
                {
                    "label": e["label"], "shared_skill_count": e["shared_skill_count"],
                    "embedding_similarity": (
                        round(e["embedding_similarity"], 3) if e["embedding_similarity"] is not None else None
                    ),
                }
                for e in entries[:3]
            ],
        }
    return facts


_NARRATIVE_PROMPT = """You are writing a short, factual explanation for a recruiter-facing dashboard.
Use ONLY the facts given below. Do not invent any detail not present in this JSON -- no skills, \
names, numbers, or claims beyond what is listed. Describe everything neutrally: every item listed \
is simply an adjacent connection in the graph, not a gap, deficiency, or something missing -- never \
say a listed course/job/peer is something the entity "lacks," "needs," or "is missing," since that \
inverts what their presence in this list actually means. If a section is empty, don't mention it. \
Write 2-4 plain sentences, no bullet points, no headings.

Facts (JSON):
{facts_json}
"""


def _facts_hash(facts: dict) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def _call_llm_for_narrative(facts: dict) -> str:
    prompt = _NARRATIVE_PROMPT.format(facts_json=json.dumps(facts, indent=2))
    try:
        return generate(_NARRATIVE_LLM_MODEL, prompt).strip()
    except Exception:
        return "(Explanation unavailable -- the local LLM service could not be reached.)"


def get_or_generate_graph_narrative(
    session: Session, entity_type: str, entity_id: int, facts: dict,
) -> str:
    """Durable, hash-invalidated version of what started as an in-process
    `st.cache_data` cache: reuses the stored narrative in
    `graph_narratives` (app.golden.models.GraphNarrative) when this
    entity's facts haven't changed since it was last generated, and only
    calls the LLM (`_call_llm_for_narrative`) when they have -- or when no
    row exists yet. Persisted, not per-process: survives a container
    restart and is shared across replicas, unlike the original cache.

    Args:
        session: an active SQLAlchemy session; this function commits.
        entity_type: `"Candidate"` or `"Job"`, matching
            build_candidate_ego_network/build_job_ego_network's node
            `type` values.
        entity_id: the candidate's or job's primary key.
        facts: `summarize_graph_facts`'s return value for this entity,
            computed fresh from Neo4j by the caller on every page load --
            cheap relative to the LLM call, so always recomputed, never
            itself cached.

    Returns:
        str: the narrative paragraph, from cache or freshly generated.
    """
    facts_hash = _facts_hash(facts)
    existing = session.execute(
        select(GraphNarrative).where(
            GraphNarrative.entity_type == entity_type, GraphNarrative.entity_id == entity_id,
        )
    ).scalar_one_or_none()
    if existing is not None and existing.facts_hash == facts_hash:
        return existing.narrative_text

    narrative_text = _call_llm_for_narrative(facts)
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.facts_hash = facts_hash
        existing.facts = facts
        existing.narrative_text = narrative_text
        existing.generated_at = now
    else:
        session.add(GraphNarrative(
            entity_type=entity_type, entity_id=entity_id, facts_hash=facts_hash,
            facts=facts, narrative_text=narrative_text, generated_at=now,
        ))
    session.commit()
    return narrative_text
