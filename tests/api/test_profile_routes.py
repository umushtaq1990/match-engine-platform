"""DB-touching tests for app/api/profile_routes.py (issue #15) — mirrors
tests/api/test_routes_db.py's TestClient + get_db dependency-override
pattern, bound to the same real-transaction-wrapped fixture session style
(tests/conftest.py's profile_db_session) so a real request goes through the
real route and a real (disposable) Postgres transaction.

Issue #26: identity now comes from a verified JWT, not a client-supplied
`keycloak_user_id` field -- tests override `get_verified_claims` the same
idiomatic way they already override `get_db`.
"""
import datetime as dt

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.jwt_auth import get_verified_claims
from app.api.main import app
from app.ingestion.source_models import Candidate

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _client_bound_to(session, sub: str = "user-abc") -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    app.dependency_overrides[get_verified_claims] = lambda: {"sub": sub}
    return TestClient(app)


def test_create_profile_returns_the_created_profile(profile_db_session):
    client = _client_bound_to(profile_db_session, sub="user-abc")
    try:
        response = client.post(
            "/profiles",
            headers=API_KEY_HEADERS,
            json={
                "role": "candidate",
                "full_name": "Ada Lovelace",
                "email": "ada@example-synthetic.test",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["keycloak_user_id"] == "user-abc"
        assert body["role"] == "candidate"
        assert body["company_name"] is None
    finally:
        app.dependency_overrides.clear()


def test_create_candidate_profile_creates_a_linked_candidate_row(profile_db_session):
    client = _client_bound_to(profile_db_session, sub="user-candidate-1")
    try:
        response = client.post(
            "/profiles",
            headers=API_KEY_HEADERS,
            json={
                "role": "candidate",
                "full_name": "Ada Lovelace",
                "email": "ada-issue17@example-synthetic.test",
            },
        )
        assert response.status_code == 201
        candidate_id = response.json()["candidate_id"]
        assert candidate_id is not None

        candidate = profile_db_session.get(Candidate, candidate_id)
        assert candidate is not None
        assert candidate.full_name == "Ada Lovelace"
        assert candidate.email == "ada-issue17@example-synthetic.test"
    finally:
        app.dependency_overrides.clear()


def test_create_employer_profile_leaves_candidate_id_null(profile_db_session):
    client = _client_bound_to(profile_db_session, sub="user-employer-1")
    try:
        response = client.post(
            "/profiles",
            headers=API_KEY_HEADERS,
            json={
                "role": "employer",
                "full_name": "Grace Hopper",
                "email": "grace-issue17@example-synthetic.test",
                "company_name": "Acme Co",
            },
        )
        assert response.status_code == 201
        assert response.json()["candidate_id"] is None
    finally:
        app.dependency_overrides.clear()


def test_create_candidate_profile_reuses_existing_candidate_with_same_email(profile_db_session):
    existing_candidate = Candidate(
        full_name="Existing Candidate", email="shared-issue17@example-synthetic.test",
        location_city="Austin", location_region="TX", years_experience=5,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    profile_db_session.add(existing_candidate)
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session, sub="user-candidate-2")
    try:
        response = client.post(
            "/profiles",
            headers=API_KEY_HEADERS,
            json={
                "role": "candidate",
                "full_name": "New Registrant",
                "email": "shared-issue17@example-synthetic.test",
            },
        )
        assert response.status_code == 201
        assert response.json()["candidate_id"] == existing_candidate.id
    finally:
        app.dependency_overrides.clear()


def test_create_profile_rejects_duplicate_keycloak_user_id(profile_db_session):
    client = _client_bound_to(profile_db_session, sub="user-dup")
    try:
        payload = {
            "role": "employer",
            "full_name": "Grace Hopper",
            "email": "grace@example-synthetic.test",
            "company_name": "Acme Co",
        }
        first = client.post("/profiles", headers=API_KEY_HEADERS, json=payload)
        assert first.status_code == 201
        second = client.post("/profiles", headers=API_KEY_HEADERS, json=payload)
        assert second.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_get_profile_returns_existing_profile(profile_db_session):
    client = _client_bound_to(profile_db_session, sub="user-xyz")
    try:
        client.post(
            "/profiles",
            headers=API_KEY_HEADERS,
            json={
                "role": "candidate",
                "full_name": "Alan Turing",
                "email": "alan@example-synthetic.test",
            },
        )
        response = client.get("/profiles/user-xyz", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        assert response.json()["full_name"] == "Alan Turing"
    finally:
        app.dependency_overrides.clear()


def test_get_profile_returns_404_when_not_found(profile_db_session):
    client = _client_bound_to(profile_db_session, sub="no-such-user")
    try:
        response = client.get("/profiles/no-such-user", headers=API_KEY_HEADERS)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_get_profile_rejects_a_caller_asking_for_someone_elses_profile(profile_db_session):
    client = _client_bound_to(profile_db_session, sub="user-someone-else")
    try:
        response = client.get("/profiles/user-xyz", headers=API_KEY_HEADERS)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
