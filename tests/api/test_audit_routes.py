"""DB-touching tests for app/audit/change_log.py's record_change helper and
app/api/audit_routes.py's GET /audit/{entity_type}/{entity_id} (issue #24).
"""
import datetime as dt

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.main import app
from app.audit.change_log import record_change
from app.audit.models import EntityChangeLog

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _client_bound_to(session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
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

    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/audit/candidate_details/999003", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["changes"]["location_city"]["new"] == "Dallas"
        assert body[1]["changes"]["location_city"]["new"] == "Austin"
    finally:
        app.dependency_overrides.clear()


def test_get_change_log_returns_empty_list_for_unknown_entity(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/audit/candidate_details/999999999", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()
