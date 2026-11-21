"""Knowledge-graph retrieval for the chatbot's `lookup_graph_context` tool
(Phase 2) -- answers relationship/similarity questions ("who else shares
candidate X's skills," "what jobs match this candidate," "what courses
would help") that neither vector search nor an exact-value lookup can.

Deliberately NOT reusing `app.golden.drilldown.graph_explainability`'s
`build_candidate_ego_network`/`build_job_ego_network` -- those also build
visualization nodes/edges and an LLM-narrated, Postgres-cached summary that
this text-only agent tool doesn't need, and importing from
`app.golden.drilldown` would tie this otherwise-portable package to one
UI's presentation module (see `app.chatbot.tools`'s own docstring for the
same precedent). These are the same bounded "rank by shared-skill count,
LIMIT before fetching" queries, trimmed to just the top-N hits -- a little
duplication of a short query is the right tradeoff here, not a shared
helper module.
"""
from app.graph.neo4j_client import get_driver

MAX_PEERS = 5
MAX_JOBS = 5
MAX_COURSES = 5

_CANDIDATE_TOP_PEERS = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:HAS_SKILL]-(p:Candidate)
WHERE p.id <> $id
WITH p, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN p.id AS id, p.full_name AS label, shared
"""
_CANDIDATE_TOP_JOBS = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:REQUIRES_SKILL]-(j:Job)
WITH j, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN j.id AS id, j.title AS label, shared
"""
_CANDIDATE_TOP_COURSES = """
MATCH (c:Candidate {id: $id})-[:HAS_SKILL]->(sk:Skill)<-[:TEACHES_SKILL]-(co:Course)
WITH co, count(DISTINCT sk) AS shared
ORDER BY shared DESC LIMIT $limit
RETURN co.id AS id, co.title AS label, shared
"""

_JOB_TOP_PEERS = """
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


def _run(query: str, center_id: int, limit: int) -> list[dict]:
    with get_driver() as driver, driver.session() as neo_session:
        return neo_session.run(query, id=center_id, limit=limit).data()


def _as_hits(rows: list[dict], key: str) -> list[dict]:
    return [{"id": r["id"], key: r["label"], "shared_skill_count": r["shared"]} for r in rows]


def graph_context_for_candidate(
    candidate_id: int, max_peers: int = MAX_PEERS, max_jobs: int = MAX_JOBS, max_courses: int = MAX_COURSES,
) -> dict:
    """Candidates/jobs/courses adjacent to `candidate_id` via shared skills,
    ranked by shared-skill count, each list capped at its `max_*` limit --
    same bounded-fan-out shape as `graph_explainability.py`'s ego-network
    queries this was trimmed from.

    Returns:
        dict: `{"subject_candidate_id": int, "similar_candidates": [{"id", "name",
            "shared_skill_count"}], "matching_jobs": [{"id", "title",
            "shared_skill_count"}], "relevant_courses": [{"id", "title",
            "shared_skill_count"}]}`. `subject_candidate_id` exists only so
            the answer-generation step's context literally states which
            candidate this data is about -- confirmed live: without it, the
            small model sometimes answered "candidate 1 is not specified in
            the provided data" despite being handed exactly candidate 1's
            results, since nothing in the result itself echoed the id back.
    """
    return {
        "subject_candidate_id": candidate_id,
        "similar_candidates": _as_hits(_run(_CANDIDATE_TOP_PEERS, candidate_id, max_peers), "name"),
        "matching_jobs": _as_hits(_run(_CANDIDATE_TOP_JOBS, candidate_id, max_jobs), "title"),
        "relevant_courses": _as_hits(_run(_CANDIDATE_TOP_COURSES, candidate_id, max_courses), "title"),
    }


def graph_context_for_job(
    job_id: int, max_peers: int = MAX_PEERS, max_jobs: int = MAX_JOBS, max_courses: int = MAX_COURSES,
) -> dict:
    """Job-side mirror of `graph_context_for_candidate`: candidates/other
    jobs/courses adjacent to `job_id` via shared required skills.

    Returns:
        dict: `{"subject_job_id": int, "matching_candidates": [{"id", "name",
            "shared_skill_count"}], "similar_jobs": [{"id", "title",
            "shared_skill_count"}], "relevant_courses": [{"id", "title",
            "shared_skill_count"}]}`. `subject_job_id` exists for the same
            reason `graph_context_for_candidate`'s `subject_candidate_id`
            does -- see its docstring.
    """
    return {
        "subject_job_id": job_id,
        "matching_candidates": _as_hits(_run(_JOB_TOP_PEERS, job_id, max_peers), "name"),
        "similar_jobs": _as_hits(_run(_JOB_TOP_JOBS, job_id, max_jobs), "title"),
        "relevant_courses": _as_hits(_run(_JOB_TOP_COURSES, job_id, max_courses), "title"),
    }
