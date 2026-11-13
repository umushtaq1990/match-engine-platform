"""DB-touching tests for app/api/job_routes.py (issue #22). Uses
matching_db_session (tests/conftest.py) since these routes read/write
Job/JobSkill, same real-transaction-wrapped pattern as
tests/api/test_routes_db.py.
"""
import datetime as dt

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.main import app
from app.ingestion.source_models import EmploymentType, Job, Occupation, Skill

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _client_bound_to(session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    return TestClient(app)


def _make_job(session, posted_by="employer-1", title="Existing Job") -> Job:
    job = Job(
        title=title, company_name="Acme Co", description_raw="desc",
        location_city="Austin", location_region="TX",
        employment_type=EmploymentType.full_time, posted_at=dt.datetime.now(dt.timezone.utc),
        posted_by_keycloak_user_id=posted_by,
    )
    session.add(job)
    session.flush()
    return job


def test_create_job_creates_job_and_split_skills(matching_db_session):
    occupation = Occupation(code="ISSUE22-OCC", title="Issue22 Occupation")
    required_skill = Skill(name="Issue22 Required Skill")
    preferred_skill = Skill(name="Issue22 Preferred Skill")
    matching_db_session.add_all([occupation, required_skill, preferred_skill])
    matching_db_session.flush()
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session)
    try:
        response = client.post(
            "/jobs", headers=API_KEY_HEADERS,
            json={
                "posted_by_keycloak_user_id": "employer-abc",
                "title": "Backend Engineer", "company_name": "Acme Co",
                "description": "Build things", "location_city": "Austin", "location_region": "TX",
                "salary_min": 90000, "salary_max": 130000,
                "primary_occupation_id": occupation.id, "experience_level": "senior",
                "required_skill_ids": [required_skill.id], "preferred_skill_ids": [preferred_skill.id],
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Backend Engineer"
        assert body["experience_level"] == "senior"
        assert body["required_skill_ids"] == [required_skill.id]
        assert body["preferred_skill_ids"] == [preferred_skill.id]
    finally:
        app.dependency_overrides.clear()


def test_list_employer_jobs_returns_only_that_employers_jobs(matching_db_session):
    _make_job(matching_db_session, posted_by="employer-1", title="Job A")
    _make_job(matching_db_session, posted_by="employer-2", title="Job B")
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session)
    try:
        response = client.get("/employers/employer-1/jobs", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        titles = [j["title"] for j in response.json()]
        assert titles == ["Job A"]
    finally:
        app.dependency_overrides.clear()


def test_get_job_details_returns_404_for_missing_job(matching_db_session):
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get("/jobs/999999999/details", headers=API_KEY_HEADERS)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_updates_fields_and_replaces_skills(matching_db_session):
    job = _make_job(matching_db_session)
    old_skill = Skill(name="Issue22 Old Skill")
    new_required = Skill(name="Issue22 New Required")
    new_preferred = Skill(name="Issue22 New Preferred")
    matching_db_session.add_all([old_skill, new_required, new_preferred])
    matching_db_session.flush()
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session)
    try:
        response = client.patch(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
            json={
                "title": "Senior Backend Engineer", "company_name": "Acme Co",
                "description": "Updated desc", "location_city": "Dallas", "location_region": "TX",
                "salary_min": 100000, "salary_max": 150000, "experience_level": "expert",
                "required_skill_ids": [new_required.id], "preferred_skill_ids": [new_preferred.id],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Senior Backend Engineer"
        assert body["location_city"] == "Dallas"
        assert body["experience_level"] == "expert"
        assert body["required_skill_ids"] == [new_required.id]
        assert body["preferred_skill_ids"] == [new_preferred.id]
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_returns_404_for_missing_job(matching_db_session):
    client = _client_bound_to(matching_db_session)
    try:
        response = client.patch(
            "/jobs/999999999/details", headers=API_KEY_HEADERS,
            json={
                "title": "X", "company_name": "Y", "description": "Z",
                "location_city": "Austin", "location_region": "TX",
            },
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_dedupes_a_skill_listed_as_both_required_and_preferred(matching_db_session):
    job = _make_job(matching_db_session)
    shared_skill = Skill(name="Issue22 Shared Skill")
    matching_db_session.add(shared_skill)
    matching_db_session.flush()
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session)
    try:
        response = client.patch(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
            json={
                "title": "X", "company_name": "Y", "description": "Z",
                "location_city": "Austin", "location_region": "TX",
                "required_skill_ids": [shared_skill.id], "preferred_skill_ids": [shared_skill.id],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["required_skill_ids"] == [shared_skill.id]
        assert body["preferred_skill_ids"] == []
    finally:
        app.dependency_overrides.clear()


def test_create_job_rejects_more_than_ten_required_skills(matching_db_session):
    client = _client_bound_to(matching_db_session)
    try:
        response = client.post(
            "/jobs", headers=API_KEY_HEADERS,
            json={
                "posted_by_keycloak_user_id": "employer-abc",
                "title": "X", "company_name": "Y", "description": "Z",
                "location_city": "Austin", "location_region": "TX",
                "required_skill_ids": list(range(1, 12)),
            },
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
