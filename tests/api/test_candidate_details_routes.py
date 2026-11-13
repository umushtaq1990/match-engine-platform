"""DB-touching tests for app/api/profile_routes.py's candidate-details/
occupations/skills endpoints (issue #18). Same TestClient + get_db
dependency-override pattern as tests/api/test_profile_routes.py, bound to
profile_db_session (real, disposable Postgres transaction) since these
routes read/write app/ingestion/source_models.py's Candidate/CandidateSkill
tables, not just user_profiles.
"""
import datetime as dt

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.main import app
from app.ingestion.source_models import Candidate, CandidateSkill, Occupation, Skill

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _client_bound_to(session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    return TestClient(app)


def _make_candidate(session, email="details-issue18@example-synthetic.test") -> Candidate:
    candidate = Candidate(
        full_name="Details Candidate", email=email, location_city="Unknown",
        location_region="Unknown", years_experience=0, created_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(candidate)
    session.flush()
    return candidate


def test_get_candidate_details_returns_defaults_with_no_skills(profile_db_session):
    candidate = _make_candidate(profile_db_session)
    profile_db_session.commit()
    client = _client_bound_to(profile_db_session)
    try:
        response = client.get(f"/candidates/{candidate.id}/details", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["location_city"] == "Unknown"
        assert body["desired_salary_min"] is None
        assert body["primary_occupation_id"] is None
        assert body["skill_ids"] == []
    finally:
        app.dependency_overrides.clear()


def test_get_candidate_details_returns_404_for_missing_candidate(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/candidates/999999999/details", headers=API_KEY_HEADERS)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_updates_location_salary_occupation_and_skills(profile_db_session):
    candidate = _make_candidate(profile_db_session, email="patch-issue18@example-synthetic.test")
    occupation = Occupation(code="ISSUE18-OCC", title="Issue 18 Test Occupation")
    skill_a = Skill(name="Issue18 Skill A")
    skill_b = Skill(name="Issue18 Skill B")
    profile_db_session.add_all([occupation, skill_a, skill_b])
    profile_db_session.flush()
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session)
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={
                "location_city": "Austin",
                "location_region": "TX",
                "desired_salary_min": 90000,
                "desired_salary_max": 120000,
                "primary_occupation_id": occupation.id,
                "skill_ids": [skill_a.id, skill_b.id],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["location_city"] == "Austin"
        assert body["desired_salary_min"] == 90000
        assert body["primary_occupation_id"] == occupation.id
        assert sorted(body["skill_ids"]) == sorted([skill_a.id, skill_b.id])

        refreshed = client.get(f"/candidates/{candidate.id}/details", headers=API_KEY_HEADERS)
        assert sorted(refreshed.json()["skill_ids"]) == sorted([skill_a.id, skill_b.id])
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_replaces_previous_skills(profile_db_session):
    candidate = _make_candidate(profile_db_session, email="replace-issue18@example-synthetic.test")
    skill_old = Skill(name="Issue18 Old Skill")
    skill_new = Skill(name="Issue18 New Skill")
    profile_db_session.add_all([skill_old, skill_new])
    profile_db_session.flush()
    profile_db_session.add(CandidateSkill(
        candidate_id=candidate.id, skill_id=skill_old.id,
        proficiency="intermediate", evidence_source="self_reported",
    ))
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session)
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={
                "location_city": "Austin", "location_region": "TX",
                "skill_ids": [skill_new.id],
            },
        )
        assert response.status_code == 200
        assert response.json()["skill_ids"] == [skill_new.id]
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_rejects_more_than_ten_skills(profile_db_session):
    candidate = _make_candidate(profile_db_session, email="toomany-issue18@example-synthetic.test")
    profile_db_session.commit()
    client = _client_bound_to(profile_db_session)
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={"location_city": "Austin", "location_region": "TX", "skill_ids": list(range(1, 12))},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_returns_404_for_missing_candidate(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.patch(
            "/candidates/999999999/details",
            headers=API_KEY_HEADERS,
            json={"location_city": "Austin", "location_region": "TX", "skill_ids": []},
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_list_occupations_returns_seeded_rows(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/occupations", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert len(body) > 0
        assert {"id", "title"} <= body[0].keys()
    finally:
        app.dependency_overrides.clear()


def test_list_skills_returns_seeded_rows(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/skills", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert len(body) > 0
        assert {"id", "name"} <= body[0].keys()
    finally:
        app.dependency_overrides.clear()
