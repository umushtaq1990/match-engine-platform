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
