"""Auth-boundary tests only — no DB required.

Matching routes are exercised against the real seeded database manually
(same convention as rank.py/data_quality.py: pure logic gets unit tests,
DB-touching orchestration gets verified against live data). These tests
cover two things without needing a DB connection:
  - the route-level gate: missing/wrong keys are rejected with 401 before
    any path function (and therefore any DB access) runs
  - the auth dependency itself, called directly: the correct key is accepted
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import require_api_key
from app.api.main import app

client = TestClient(app)


def test_health_does_not_require_api_key():
    response = client.get("/health")
    assert response.status_code == 200


def test_matching_route_rejects_missing_api_key():
    response = client.get("/candidates/1/job-matches")
    assert response.status_code == 401


def test_semantic_matching_route_rejects_missing_api_key():
    response = client.get("/candidates/1/job-matches/semantic")
    assert response.status_code == 401


def test_profiles_post_route_rejects_missing_api_key():
    response = client.post("/profiles", json={
        "keycloak_user_id": "user-1", "role": "candidate",
        "full_name": "Test User", "email": "test@example-synthetic.test",
    })
    assert response.status_code == 401


def test_profiles_get_route_rejects_missing_api_key():
    response = client.get("/profiles/user-1")
    assert response.status_code == 401


def test_candidate_details_get_route_rejects_missing_api_key():
    response = client.get("/candidates/1/details")
    assert response.status_code == 401


def test_candidate_details_patch_route_rejects_missing_api_key():
    response = client.patch("/candidates/1/details", json={
        "location_city": "Austin", "location_region": "TX", "skill_ids": [],
    })
    assert response.status_code == 401


def test_occupations_route_rejects_missing_api_key():
    response = client.get("/occupations")
    assert response.status_code == 401


def test_create_occupation_route_rejects_missing_api_key():
    response = client.post("/occupations", json={"title": "Test Occupation"})
    assert response.status_code == 401


def test_skills_route_rejects_missing_api_key():
    response = client.get("/skills")
    assert response.status_code == 401


def test_create_skill_route_rejects_missing_api_key():
    response = client.post("/skills", json={"name": "Test Skill"})
    assert response.status_code == 401


def test_create_job_route_rejects_missing_api_key():
    response = client.post("/jobs", json={
        "posted_by_keycloak_user_id": "employer-1", "title": "X", "company_name": "Y",
        "description": "Z", "location_city": "Austin", "location_region": "TX",
    })
    assert response.status_code == 401


def test_list_employer_jobs_route_rejects_missing_api_key():
    response = client.get("/employers/employer-1/jobs")
    assert response.status_code == 401


def test_job_details_get_route_rejects_missing_api_key():
    response = client.get("/jobs/1/details")
    assert response.status_code == 401


def test_job_details_patch_route_rejects_missing_api_key():
    response = client.patch("/jobs/1/details", json={
        "title": "X", "company_name": "Y", "description": "Z",
        "location_city": "Austin", "location_region": "TX",
    })
    assert response.status_code == 401


def test_matching_route_rejects_wrong_api_key():
    response = client.get("/candidates/1/job-matches", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_require_api_key_rejects_none():
    with pytest.raises(HTTPException) as exc_info:
        require_api_key(None)
    assert exc_info.value.status_code == 401


def test_require_api_key_rejects_wrong_value(monkeypatch):
    monkeypatch.setenv("JME_API_KEY", "test-key-123")
    with pytest.raises(HTTPException) as exc_info:
        require_api_key("wrong-key")
    assert exc_info.value.status_code == 401


def test_require_api_key_accepts_configured_value(monkeypatch):
    monkeypatch.setenv("JME_API_KEY", "test-key-123")
    require_api_key("test-key-123")  # does not raise
