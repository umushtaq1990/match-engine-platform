"""Auth-boundary tests only — no DB/LLM required. Same convention as
tests/api/test_auth.py and tests/analytics/test_auth.py.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.extraction.deps import require_api_key
from app.extraction.main import app

client = TestClient(app)


def test_health_does_not_require_api_key():
    response = client.get("/health")
    assert response.status_code == 200


def test_extract_route_rejects_missing_api_key():
    response = client.post("/extract", json={"text": "Experience with Python required."})
    assert response.status_code == 401


def test_extract_route_rejects_wrong_api_key():
    response = client.post(
        "/extract",
        json={"text": "Experience with Python required."},
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
