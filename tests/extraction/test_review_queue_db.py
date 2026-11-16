"""DB-touching, route-level tests for the human-in-the-loop review queue —
/extract's persistence side effect (ExtractionCall) and the /review-queue
endpoints. extract_skills itself is monkeypatched so these tests don't need
a real Ollama call, same spirit as test_cv_routes_db.py's fake object store.

extraction_calls isn't one of matching_db_session's TRUNCATEd tables (same
as candidate_documents, extraction_runs -- see tests/conftest.py), so these
assert against rows scoped to this test's own ids, never the table's total
count, which could include rows from a real extraction service running
against the same dev database outside the test transaction.
"""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.extraction import routes
from app.extraction.deps import get_db
from app.extraction.main import app
from app.extraction.models import ExtractionCall

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _client_bound_to(session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    return TestClient(app)


def _fake_extract_skills(needs_review: bool):
    def _inner(text, session, llm_model=None, embedding_model=None, similarity_threshold=None):
        return {
            "matches": [
                {"skill_id": 1, "requirement_level": "required", "confidence": 0.9, "source_phrase": "Python"}
            ],
            "unmatched": [],
            "extracted": [{"phrase": "Python", "requirement_level": "required"}],
            "document_confidence": 0.2 if needs_review else 0.9,
            "needs_review": needs_review,
        }
    return _inner


def test_extract_persists_an_extraction_call_row(matching_db_session, monkeypatch):
    monkeypatch.setattr(routes, "extract_skills", _fake_extract_skills(needs_review=False))
    client = _client_bound_to(matching_db_session)
    try:
        response = client.post(
            "/extract", json={"text": "Must know Python."}, headers=API_KEY_HEADERS
        )
        assert response.status_code == 200
        body = response.json()
        assert body["document_confidence"] == 0.9
        assert body["needs_review"] is False

        rows = matching_db_session.execute(
            select(ExtractionCall).where(ExtractionCall.text_length == len("Must know Python."))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].needs_review is False
    finally:
        app.dependency_overrides.clear()


def test_review_queue_lists_only_unreviewed_flagged_calls(matching_db_session):
    flagged_unreviewed = ExtractionCall(
        called_at=datetime.now(timezone.utc), llm_model="qwen2.5:1.5b",
        embedding_model="nomic-embed-text", similarity_threshold=0.65,
        text_length=10, match_count=0, unmatched_count=1,
        document_confidence=0.1, needs_review=True,
    )
    flagged_already_reviewed = ExtractionCall(
        called_at=datetime.now(timezone.utc), llm_model="qwen2.5:1.5b",
        embedding_model="nomic-embed-text", similarity_threshold=0.65,
        text_length=10, match_count=0, unmatched_count=1,
        document_confidence=0.1, needs_review=True,
        reviewed_at=datetime.now(timezone.utc), reviewer="alice",
    )
    not_flagged = ExtractionCall(
        called_at=datetime.now(timezone.utc), llm_model="qwen2.5:1.5b",
        embedding_model="nomic-embed-text", similarity_threshold=0.65,
        text_length=10, match_count=3, unmatched_count=0,
        document_confidence=0.95, needs_review=False,
    )
    matching_db_session.add_all([flagged_unreviewed, flagged_already_reviewed, not_flagged])
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session)
    try:
        response = client.get("/review-queue", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()}
        assert flagged_unreviewed.id in ids
        assert flagged_already_reviewed.id not in ids
        assert not_flagged.id not in ids
    finally:
        app.dependency_overrides.clear()


def test_mark_reviewed_removes_call_from_the_queue(matching_db_session):
    flagged = ExtractionCall(
        called_at=datetime.now(timezone.utc), llm_model="qwen2.5:1.5b",
        embedding_model="nomic-embed-text", similarity_threshold=0.65,
        text_length=10, match_count=0, unmatched_count=1,
        document_confidence=0.1, needs_review=True,
    )
    matching_db_session.add(flagged)
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session)
    try:
        review_response = client.post(
            f"/review-queue/{flagged.id}/review", json={"reviewer": "alice"}, headers=API_KEY_HEADERS
        )
        assert review_response.status_code == 200
        assert review_response.json()["reviewer"] == "alice"

        queue_response = client.get("/review-queue", headers=API_KEY_HEADERS)
        ids = {row["id"] for row in queue_response.json()}
        assert flagged.id not in ids
    finally:
        app.dependency_overrides.clear()


def test_mark_reviewed_returns_404_for_unknown_call(matching_db_session):
    client = _client_bound_to(matching_db_session)
    try:
        response = client.post(
            "/review-queue/999999/review", json={"reviewer": "alice"}, headers=API_KEY_HEADERS
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
