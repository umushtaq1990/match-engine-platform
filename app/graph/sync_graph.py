"""Batch ETL sync from Postgres into the Neo4j knowledge graph (Phase 19).

Run with: python -m app.graph.sync_graph

Idempotent by construction (`MERGE`, not `CREATE`) — safe to re-run. Not a
live dual-write: this reads a snapshot of Postgres and pushes it into
Neo4j, reflecting the graph as of whenever this job last ran, not the
current instant. See docs/knowledge-graph.md.

Every node/edge type here mirrors an existing relational table/junction
table exactly, except ENGAGED_WITH, which aggregates Phase 14's
`analytics.behavioral_events` log — a deliberate cross-phase tie-in, not
new data.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.models import BehavioralEvent
from app.db import SessionLocal
from app.graph.neo4j_client import get_driver
from app.ingestion.source_models import (
    Candidate,
    CandidateSkill,
    Course,
    CourseSkill,
    Job,
    JobSkill,
    Occupation,
    OccupationSkill,
    Skill,
)

BATCH_SIZE = 1000

# One uniqueness constraint per node label — makes MERGE lookups during edge
# creation an index seek, not a label scan, and doubles as schema
# documentation of what a "node" means in this graph.
_CONSTRAINTS = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Candidate) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (j:Job) REQUIRE j.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Skill) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (o:Occupation) REQUIRE o.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (co:Course) REQUIRE co.id IS UNIQUE",
]


def _run_batched(neo_session, query: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), BATCH_SIZE):
        neo_session.run(query, batch=rows[i:i + BATCH_SIZE])


def _sync_nodes(pg: Session, neo_session) -> dict[str, int]:
    counts: dict[str, int] = {}

    skills = [
        {"id": s.id, "name": s.name, "category": s.category}
        for s in pg.execute(select(Skill)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MERGE (s:Skill {id: row.id})
        SET s.name = row.name, s.category = row.category
    """, skills)
    counts["Skill"] = len(skills)

    occupations = [
        {"id": o.id, "code": o.code, "title": o.title}
        for o in pg.execute(select(Occupation)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MERGE (o:Occupation {id: row.id})
        SET o.code = row.code, o.title = row.title
    """, occupations)
    counts["Occupation"] = len(occupations)

    courses = [
        {"id": c.id, "title": c.title, "provider": c.provider, "level": c.level}
        for c in pg.execute(select(Course)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MERGE (co:Course {id: row.id})
        SET co.title = row.title, co.provider = row.provider, co.level = row.level
    """, courses)
    counts["Course"] = len(courses)

    candidates = [
        {
            "id": c.id, "full_name": c.full_name, "years_experience": c.years_experience,
            "location_city": c.location_city, "location_region": c.location_region,
        }
        for c in pg.execute(select(Candidate)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MERGE (c:Candidate {id: row.id})
        SET c.full_name = row.full_name, c.years_experience = row.years_experience,
            c.location_city = row.location_city, c.location_region = row.location_region
    """, candidates)
    counts["Candidate"] = len(candidates)

    jobs = [
        {
            "id": j.id, "title": j.title, "company_name": j.company_name,
            "location_city": j.location_city, "location_region": j.location_region,
        }
        for j in pg.execute(select(Job)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MERGE (j:Job {id: row.id})
        SET j.title = row.title, j.company_name = row.company_name,
            j.location_city = row.location_city, j.location_region = row.location_region
    """, jobs)
    counts["Job"] = len(jobs)

    return counts


def _sync_edges(pg: Session, neo_session) -> dict[str, int]:
    counts: dict[str, int] = {}

    has_skill = [
        {"candidate_id": cs.candidate_id, "skill_id": cs.skill_id, "proficiency": cs.proficiency.value}
        for cs in pg.execute(select(CandidateSkill)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MATCH (c:Candidate {id: row.candidate_id}), (s:Skill {id: row.skill_id})
        MERGE (c)-[r:HAS_SKILL]->(s)
        SET r.proficiency = row.proficiency
    """, has_skill)
    counts["HAS_SKILL"] = len(has_skill)

    requires_skill = [
        {"job_id": js.job_id, "skill_id": js.skill_id, "requirement_level": js.requirement_level.value}
        for js in pg.execute(select(JobSkill)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MATCH (j:Job {id: row.job_id}), (s:Skill {id: row.skill_id})
        MERGE (j)-[r:REQUIRES_SKILL]->(s)
        SET r.requirement_level = row.requirement_level
    """, requires_skill)
    counts["REQUIRES_SKILL"] = len(requires_skill)

    skill_of_occupation = [
        {"occupation_id": os_.occupation_id, "skill_id": os_.skill_id, "importance": float(os_.importance)}
        for os_ in pg.execute(select(OccupationSkill)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MATCH (o:Occupation {id: row.occupation_id}), (s:Skill {id: row.skill_id})
        MERGE (o)-[r:SKILL_OF_OCCUPATION]->(s)
        SET r.importance = row.importance
    """, skill_of_occupation)
    counts["SKILL_OF_OCCUPATION"] = len(skill_of_occupation)

    teaches_skill = [
        {"course_id": cs.course_id, "skill_id": cs.skill_id, "coverage_weight": float(cs.coverage_weight)}
        for cs in pg.execute(select(CourseSkill)).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MATCH (co:Course {id: row.course_id}), (s:Skill {id: row.skill_id})
        MERGE (co)-[r:TEACHES_SKILL]->(s)
        SET r.coverage_weight = row.coverage_weight
    """, teaches_skill)
    counts["TEACHES_SKILL"] = len(teaches_skill)

    targets_occupation = [
        {"candidate_id": c.id, "occupation_id": c.primary_occupation_id}
        for c in pg.execute(
            select(Candidate).where(Candidate.primary_occupation_id.isnot(None))
        ).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MATCH (c:Candidate {id: row.candidate_id}), (o:Occupation {id: row.occupation_id})
        MERGE (c)-[:TARGETS_OCCUPATION]->(o)
    """, targets_occupation)
    counts["TARGETS_OCCUPATION"] = len(targets_occupation)

    posted_for_occupation = [
        {"job_id": j.id, "occupation_id": j.occupation_id}
        for j in pg.execute(select(Job).where(Job.occupation_id.isnot(None))).scalars().all()
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MATCH (j:Job {id: row.job_id}), (o:Occupation {id: row.occupation_id})
        MERGE (j)-[:POSTED_FOR_OCCUPATION]->(o)
    """, posted_for_occupation)
    counts["POSTED_FOR_OCCUPATION"] = len(posted_for_occupation)

    engagement_rows = pg.execute(
        select(
            BehavioralEvent.actor_id,
            BehavioralEvent.entity_id,
            func.count().label("event_count"),
            func.max(BehavioralEvent.received_at).label("last_event_at"),
        )
        .where(BehavioralEvent.actor_type == "candidate", BehavioralEvent.entity_type == "job")
        .group_by(BehavioralEvent.actor_id, BehavioralEvent.entity_id)
    ).all()
    engaged_with = [
        {
            "candidate_id": row.actor_id, "job_id": row.entity_id,
            "event_count": row.event_count, "last_event_at": row.last_event_at.isoformat(),
        }
        for row in engagement_rows
    ]
    _run_batched(neo_session, """
        UNWIND $batch AS row
        MATCH (c:Candidate {id: row.candidate_id}), (j:Job {id: row.job_id})
        MERGE (c)-[r:ENGAGED_WITH]->(j)
        SET r.event_count = row.event_count, r.last_event_at = row.last_event_at
    """, engaged_with)
    counts["ENGAGED_WITH"] = len(engaged_with)

    return counts


def sync() -> dict[str, dict[str, int]]:
    pg = SessionLocal()
    driver = get_driver()
    try:
        with driver.session() as neo_session:
            for constraint in _CONSTRAINTS:
                neo_session.run(constraint)
            node_counts = _sync_nodes(pg, neo_session)
            edge_counts = _sync_edges(pg, neo_session)
        return {"nodes": node_counts, "edges": edge_counts}
    finally:
        pg.close()
        driver.close()


if __name__ == "__main__":
    result = sync()
    print("Graph sync complete:")
    print("  Nodes:", result["nodes"])
    print("  Edges:", result["edges"])
