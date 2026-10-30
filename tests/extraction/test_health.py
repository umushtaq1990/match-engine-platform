from fastapi.testclient import TestClient

from app.extraction.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness():
    response = client.get("/readiness")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
