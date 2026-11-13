"""DB-touching tests for app/audit/change_log.py's record_change helper and
app/api/audit_routes.py's GET /audit/{entity_type}/{entity_id} (issue #24).

Issue #26: the GET route is strict-ownership -- a caller must own the
entity (a UserProfile linking candidate_id to their `sub`, for
candidate_details) to read its history at all, even if that history is
empty.
"""
import datetime as dt

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.jwt_auth import get_verified_claims
from app.api.main import app
from app.audit.change_log import record_change
from app.audit.models import EntityChangeLog
from app.identity.models import UserProfile, UserRole
from app.ingestion.source_models import Candidate


def _make_candidate_with_id(session, candidate_id: int, email: str) -> None:
    """UserProfile.candidate_id has a real FK to candidates.id -- these
    tests need a Candidate row at an exact, predictable id to link a
    UserProfile to for the ownership check.
    """
    session.add(Candidate(
        id=candidate_id, full_name="Audit Test Candidate", email=email,
        location_city="Unknown", location_region="Unknown", years_experience=0,
        created_at=dt.datetime.now(dt.timezone.utc),
    ))

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _client_bound_to(session, sub: str = "user-abc") -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    app.dependency_overrides[get_verified_claims] = lambda: {"sub": sub}
    return TestClient(app)


def test_record_change_logs_only_actually_changed_fields(profile_db_session):
    record_change(
        profile_db_session, "candidate_details", 999001, "user-abc",
        old={"location_city": "Austin", "salary_min": 90000},
        new={"location_city": "Dallas", "salary_min": 90000},
    )
    profile_db_session.commit()

    row = profile_db_session.query(EntityChangeLog).filter_by(entity_type="candidate_details", entity_id=999001).first()
    assert row is not None
    assert row.changes == {"location_city": {"old": "Austin", "new": "Dallas"}}


def test_record_change_writes_nothing_on_a_no_op_update(profile_db_session):
    record_change(
        profile_db_session, "candidate_details", 999002, "user-abc",
        old={"location_city": "Austin"}, new={"location_city": "Austin"},
    )
    profile_db_session.commit()

    count = profile_db_session.query(EntityChangeLog).filter_by(entity_type="candidate_details", entity_id=999002).count()
    assert count == 0


def test_get_change_log_returns_history_newest_first(profile_db_session):
    _make_candidate_with_id(profile_db_session, 999003, "history-issue26@example-synthetic.test")
    profile_db_session.flush()
    profile_db_session.add(UserProfile(
        keycloak_user_id="user-abc", role=UserRole.candidate, full_name="History Owner",
        email="history-issue26@example-synthetic.test", candidate_id=999003,
    ))
    older = EntityChangeLog(
        entity_type="candidate_details", entity_id=999003, changed_by_keycloak_user_id="user-abc",
        changed_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), changes={"location_city": {"old": None, "new": "Austin"}},
    )
    newer = EntityChangeLog(
        entity_type="candidate_details", entity_id=999003, changed_by_keycloak_user_id="user-abc",
        changed_at=dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc), changes={"location_city": {"old": "Austin", "new": "Dallas"}},
    )
    profile_db_session.add_all([older, newer])
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session, sub="user-abc")
    try:
        response = client.get("/audit/candidate_details/999003", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["changes"]["location_city"]["new"] == "Dallas"
        assert body[1]["changes"]["location_city"]["new"] == "Austin"
    finally:
        app.dependency_overrides.clear()


def test_get_change_log_returns_empty_list_when_owner_has_no_history_yet(profile_db_session):
    _make_candidate_with_id(profile_db_session, 999004, "nohistory-issue26@example-synthetic.test")
    profile_db_session.flush()
    profile_db_session.add(UserProfile(
        keycloak_user_id="user-no-history", role=UserRole.candidate, full_name="No History Owner",
        email="nohistory-issue26@example-synthetic.test", candidate_id=999004,
    ))
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session, sub="user-no-history")
    try:
        response = client.get("/audit/candidate_details/999004", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()


def test_get_change_log_rejects_a_caller_who_doesnt_own_the_entity(profile_db_session):
    _make_candidate_with_id(profile_db_session, 999005, "realowner-issue26@example-synthetic.test")
    profile_db_session.flush()
    profile_db_session.add(UserProfile(
        keycloak_user_id="user-real-owner", role=UserRole.candidate, full_name="Real Owner",
        email="realowner-issue26@example-synthetic.test", candidate_id=999005,
    ))
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session, sub="user-impostor")
    try:
        response = client.get("/audit/candidate_details/999005", headers=API_KEY_HEADERS)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_get_change_log_rejects_an_entirely_unowned_entity(profile_db_session):
    client = _client_bound_to(profile_db_session, sub="anyone")
    try:
        response = client.get("/audit/candidate_details/999999999", headers=API_KEY_HEADERS)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_get_change_log_returns_404_for_unknown_entity_type(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/audit/not_a_real_entity_type/1", headers=API_KEY_HEADERS)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
