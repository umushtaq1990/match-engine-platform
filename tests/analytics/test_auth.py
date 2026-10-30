"""Auth-boundary tests only — no DB required. Same convention as
tests/api/test_auth.py: event-ingestion/query routes are exercised against
the real DB manually, these tests only cover the 401 gate and the auth
dependency itself.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.analytics.deps import require_api_key
from app.analytics.main import app

client = TestClient(app)


def test_health_does_not_require_api_key():
    response = client.get("/health")
    assert response.status_code == 200


def test_events_route_rejects_missing_api_key():
    response = client.post("/events", json={"actor_type": "candidate", "actor_id": 1, "event_type": "login"})
    assert response.status_code == 401


def test_events_route_rejects_wrong_api_key():
    response = client.post(
        "/events",
        json={"actor_type": "candidate", "actor_id": 1, "event_type": "login"},
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 401


def test_require_api_key_rejects_none():
    with pytest.raises(HTTPException) as exc_info:
        require_api_key(None)
    assert exc_info.value.status_code == 401


def test_require_api_key_accepts_configured_value(monkeypatch):
    monkeypatch.setenv("JME_API_KEY", "test-key-123")
    require_api_key("test-key-123")  # does not raise
