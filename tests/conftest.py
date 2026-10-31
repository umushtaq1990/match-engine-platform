"""Shared pytest fixtures for DB-touching tests (tests/matching/test_rank_db.py,
tests/api/test_routes_db.py) — closing the test-coverage gap `docs/
production-readiness.md` flagged: `rank.py`'s orchestration and route-level
API business behavior previously had zero automated tests, only manual
verification against live data.

`matching_db_session` wraps an entire test in one real Postgres transaction:
it TRUNCATEs the core matching tables, lets the test insert small, explicit
fixture rows with predictable IDs, and rolls back at the end — the real
seeded dataset is untouched outside the test. This relies on
`join_transaction_mode="create_savepoint"`: the session's own commit() only
releases a SAVEPOINT, not the outer transaction, so the final rollback below
undoes everything including the TRUNCATE. Verified directly (not assumed)
against a disposable table before trusting it against the real dataset: a
commit-then-rollback cycle left a new connection seeing only the
pre-existing row, never the fixture row.

Skipped automatically (not failed) if no database is reachable, e.g. a
contributor without Docker running.
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import DATABASE_URL
from app.ingestion.source_models import (
    Candidate,
    CandidateSkill,
    EmploymentType,
    EvidenceSource,
    Job,
    JobSkill,
    Occupation,
    ProficiencyLevel,
    RequirementLevel,
    Skill,
)

_TRUNCATE_TABLES = (
    "job_skills", "jobs", "candidate_skill_assessments", "candidate_skills",
    "candidates", "course_skills", "courses", "occupation_skills", "occupations",
    "skill_synonyms", "skills",
)


@pytest.fixture
def matching_db_session():
    try:
        engine = create_engine(DATABASE_URL)
        connection = engine.connect()
    except Exception as exc:
        pytest.skip(f"No reachable database for DB-touching tests: {exc}")

    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    session.execute(text(f"TRUNCATE TABLE {', '.join(_TRUNCATE_TABLES)} RESTART IDENTITY CASCADE"))
    session.commit()

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()


@pytest.fixture
def analytics_db_session():
    """Same real-transaction-wrapped pattern as `matching_db_session`, scoped
    to the analytics service's own `analytics.behavioral_events` table
    (Phase 14) instead of the matching engine's tables — used by Phase 24's
    segmentation tests (tests/analytics/test_segmentation_db.py).
    """
    try:
        engine = create_engine(DATABASE_URL)
        connection = engine.connect()
    except Exception as exc:
        pytest.skip(f"No reachable database for DB-touching tests: {exc}")

    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    session.execute(text("TRUNCATE TABLE analytics.behavioral_events RESTART IDENTITY CASCADE"))
    session.commit()

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()


def seed_matching_fixture(session: Session) -> dict:
    """One job (Python required, SQL preferred) and two candidates (one
    eligible match, one not) with hand-computable scores — shared by
    tests/matching/test_rank_db.py and tests/api/test_routes_db.py so both
    exercise the exact same fixture data at different layers (orchestration
    vs HTTP).
    """
    python = Skill(name="Python", category="programming")
    sql = Skill(name="SQL", category="data")
    session.add_all([python, sql])
    session.flush()

    occupation = Occupation(code="OCC-TEST", title="Data Analyst")
    session.add(occupation)
    session.flush()

    job = Job(
        title="Data Analyst", company_name="Acme Co", occupation_id=occupation.id,
        description_raw="Analyze data.", location_city="Austin", location_region="TX",
        salary_min=50_000, salary_max=70_000, employment_type=EmploymentType.full_time,
        posted_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(job)
    session.flush()
    session.add_all([
        JobSkill(job_id=job.id, skill_id=python.id, requirement_level=RequirementLevel.required),
        JobSkill(job_id=job.id, skill_id=sql.id, requirement_level=RequirementLevel.preferred),
    ])

    matching_candidate = Candidate(
        full_name="Matching Candidate", email="match@example-synthetic.test",
        location_city="Austin", location_region="TX", years_experience=3,
        desired_salary_min=55_000, desired_salary_max=65_000,
        primary_occupation_id=occupation.id,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    non_matching_candidate = Candidate(
        full_name="Non-Matching Candidate", email="nomatch@example-synthetic.test",
        location_city="Other City", location_region="Other State", years_experience=1,
        desired_salary_min=None, desired_salary_max=None,
        primary_occupation_id=None,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add_all([matching_candidate, non_matching_candidate])
    session.flush()
    session.add(CandidateSkill(
        candidate_id=matching_candidate.id, skill_id=python.id,
        proficiency=ProficiencyLevel.advanced, evidence_source=EvidenceSource.self_reported,
    ))
    session.commit()

    return {
        "job_id": job.id, "python_id": python.id, "sql_id": sql.id,
        "matching_candidate_id": matching_candidate.id,
        "non_matching_candidate_id": non_matching_candidate.id,
    }
