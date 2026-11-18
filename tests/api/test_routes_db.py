"""DB-touching, route-level API tests — business behavior (ranking order,
skill-gap content, 404s), not just the auth boundary already covered in
tests/api/test_auth.py. Overrides the `get_db` dependency to point at the
same transaction-wrapped fixture session used by tests/matching/
test_rank_db.py, so a real request goes through the real FastAPI route,
the real matching orchestration, and a real (disposable) Postgres
transaction — not a mock at any layer.
"""
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.deps import get_db
from app.api.jwt_auth import get_verified_claims
from app.api.main import app
from app.embeddings.models import EntityEmbedding
from tests.conftest import seed_matching_fixture

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _clear_embeddings(session, entity_ids: dict[str, int]) -> None:
    """entity_embeddings isn't truncated by `matching_db_session` (no FK
    into the tables it does truncate -- see tests/embeddings/
    test_retrieval_db.py's module docstring), so a real embeddings-compute
    run against the live seeded dataset can leave rows at the same ids
    `seed_matching_fixture` reuses after RESTART IDENTITY, silently
    flipping `X-Ranking-Mode` from the "no embeddings" path these tests
    assert to "blended"."""
    session.execute(
        delete(EntityEmbedding).where(
            EntityEmbedding.entity_type.in_(list(entity_ids.keys())),
            EntityEmbedding.entity_id.in_(list(entity_ids.values())),
        )
    )
    session.commit()


def _client_bound_to(session, sub: str = "test-caller") -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    app.dependency_overrides[get_verified_claims] = lambda: {"sub": sub}
    return TestClient(app)


def test_job_matches_route_returns_real_scored_match(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)
    _clear_embeddings(matching_db_session, {
        "candidate": ids["matching_candidate_id"], "job": ids["job_id"],
    })
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get(
            f"/candidates/{ids['matching_candidate_id']}/job-matches", headers=API_KEY_HEADERS
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["entity_id"] == ids["job_id"]
        assert body[0]["eligible"] is True
        assert body[0]["score"]["total"] == 0.79
        assert body[0]["skill_gap"]["not_recorded_preferred"] == [ids["sql_id"]]
        # Phase 21: cache-aside is wired in, but there's no Redis container in
        # this test run (REDIS_URL unset) — every request is a documented
        # fail-open miss, not a silent no-op. See app/api/cache.py.
        assert response.headers["X-Cache"] == "MISS"
        # Phase 22: default mode is "blended", but this fixture has no
        # embeddings computed, so it falls back to rule-based order
        # (unchanged scores, as asserted above) and reports that honestly.
        assert response.headers["X-Ranking-Mode"] == "rule_based_no_embeddings"
    finally:
        app.dependency_overrides.clear()


def test_job_matches_route_mode_rule_based_skips_the_blended_path(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get(
            f"/candidates/{ids['matching_candidate_id']}/job-matches?mode=rule_based",
            headers=API_KEY_HEADERS,
        )
        assert response.status_code == 200
        assert response.headers["X-Ranking-Mode"] == "rule_based"
        assert response.json()[0]["score"]["total"] == 0.79
    finally:
        app.dependency_overrides.clear()


def test_job_matches_route_excludes_ineligible_candidate_by_default(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get(
            f"/candidates/{ids['non_matching_candidate_id']}/job-matches", headers=API_KEY_HEADERS
        )
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()


def test_job_matches_route_returns_404_for_unknown_candidate(matching_db_session):
    seed_matching_fixture(matching_db_session)
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get("/candidates/999999/job-matches", headers=API_KEY_HEADERS)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_candidate_matches_route_ranks_matching_candidate_first(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get(
            f"/jobs/{ids['job_id']}/candidate-matches?include_ineligible=true",
            headers=API_KEY_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert [m["entity_id"] for m in body] == [
            ids["matching_candidate_id"], ids["non_matching_candidate_id"],
        ]
    finally:
        app.dependency_overrides.clear()


def test_candidate_matches_route_returns_404_for_unknown_job(matching_db_session):
    seed_matching_fixture(matching_db_session)
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get("/jobs/999999/candidate-matches", headers=API_KEY_HEADERS)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
