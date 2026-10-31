"""DB-touching, route-level tests for CV upload/download — real FastAPI
routes, real (transaction-wrapped, rolled-back) Postgres, object storage
monkeypatched to an in-memory fake so CI doesn't need a real SeaweedFS
container for this test (a real end-to-end run against the actual service
is verified manually/in the compose smoke test, not duplicated here).
"""
from fastapi.testclient import TestClient

from app.extraction import document_storage
from app.extraction.deps import get_db
from app.extraction.main import app
from tests.conftest import seed_matching_fixture

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}
VALID_PDF_BYTES = b"%PDF-1.4\n%fake minimal pdf content for a test\n%%EOF"


class _FakeObjectStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def upload_cv(self, candidate_id, data):
        key = document_storage.object_key(candidate_id)
        self.objects[key] = data
        return key

    def download_cv(self, candidate_id):
        key = document_storage.object_key(candidate_id)
        if key not in self.objects:
            raise document_storage.StorageUnavailableError("not found")
        return self.objects[key]


def _client_bound_to(session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    return TestClient(app)


def test_upload_cv_rejects_non_pdf_content_type(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)
    client = _client_bound_to(matching_db_session)
    try:
        response = client.post(
            f"/candidates/{ids['matching_candidate_id']}/cv",
            files={"file": ("resume.png", b"not a pdf", "image/png")},
            headers=API_KEY_HEADERS,
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_upload_cv_returns_404_for_unknown_candidate(matching_db_session):
    seed_matching_fixture(matching_db_session)
    client = _client_bound_to(matching_db_session)
    try:
        response = client.post(
            "/candidates/999999/cv",
            files={"file": ("cv.pdf", VALID_PDF_BYTES, "application/pdf")},
            headers=API_KEY_HEADERS,
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_upload_then_fetch_metadata_then_download_round_trip(matching_db_session, monkeypatch):
    fake_store = _FakeObjectStore()
    monkeypatch.setattr(document_storage, "upload_cv", fake_store.upload_cv)
    monkeypatch.setattr(document_storage, "download_cv", fake_store.download_cv)

    ids = seed_matching_fixture(matching_db_session)
    candidate_id = ids["matching_candidate_id"]
    client = _client_bound_to(matching_db_session)
    try:
        upload_response = client.post(
            f"/candidates/{candidate_id}/cv",
            files={"file": ("my_resume.pdf", VALID_PDF_BYTES, "application/pdf")},
            headers=API_KEY_HEADERS,
        )
        assert upload_response.status_code == 201
        body = upload_response.json()
        assert body["candidate_id"] == candidate_id
        assert body["original_filename"] == "my_resume.pdf"
        assert body["size_bytes"] == len(VALID_PDF_BYTES)

        metadata_response = client.get(f"/candidates/{candidate_id}/cv", headers=API_KEY_HEADERS)
        assert metadata_response.status_code == 200
        assert metadata_response.json()["original_filename"] == "my_resume.pdf"

        download_response = client.get(
            f"/candidates/{candidate_id}/cv/download", headers=API_KEY_HEADERS
        )
        assert download_response.status_code == 200
        assert download_response.content == VALID_PDF_BYTES
        assert download_response.headers["content-type"] == "application/pdf"
    finally:
        app.dependency_overrides.clear()


def test_reupload_overwrites_prior_metadata_not_duplicates_it(matching_db_session, monkeypatch):
    fake_store = _FakeObjectStore()
    monkeypatch.setattr(document_storage, "upload_cv", fake_store.upload_cv)
    monkeypatch.setattr(document_storage, "download_cv", fake_store.download_cv)

    ids = seed_matching_fixture(matching_db_session)
    candidate_id = ids["matching_candidate_id"]
    client = _client_bound_to(matching_db_session)
    try:
        client.post(
            f"/candidates/{candidate_id}/cv",
            files={"file": ("v1.pdf", VALID_PDF_BYTES, "application/pdf")},
            headers=API_KEY_HEADERS,
        )
        second = client.post(
            f"/candidates/{candidate_id}/cv",
            files={"file": ("v2.pdf", VALID_PDF_BYTES + b"more", "application/pdf")},
            headers=API_KEY_HEADERS,
        )
        assert second.status_code == 201
        assert second.json()["original_filename"] == "v2.pdf"

        metadata_response = client.get(f"/candidates/{candidate_id}/cv", headers=API_KEY_HEADERS)
        assert metadata_response.json()["original_filename"] == "v2.pdf"
    finally:
        app.dependency_overrides.clear()


def test_get_cv_metadata_returns_404_when_none_uploaded(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get(
            f"/candidates/{ids['non_matching_candidate_id']}/cv", headers=API_KEY_HEADERS
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
