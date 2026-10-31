"""CV document storage (Use Case C): candidates can upload their CV as a
PDF, stored in SeaweedFS's S3-compatible API — not MinIO, which stopped
publishing public container images in 2025 (confirmed directly: `docker
pull minio/minio` returns "pull access denied, repository does not exist"
and both Docker Hub's and Quay's APIs refuse unauthenticated tag listing).
SeaweedFS's `chrislusf/seaweedfs` image is still freely pullable, open
source, and exposes the same S3 API any client library already speaks, so
switching later costs nothing beyond an endpoint URL.

Deliberately basic, per the brief: PDF only, 2MB cap, no virus scanning, no
per-candidate access control beyond this service's existing X-API-Key gate.
Stores only object metadata in Postgres (app/extraction/models.py's
CandidateDocument) — the actual bytes live in object storage, never in the
relational database.
"""
import io
import logging
import os

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

MAX_CV_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB — a real multi-page text CV fits comfortably.
ALLOWED_CONTENT_TYPE = "application/pdf"
PDF_MAGIC_BYTES = b"%PDF-"
BUCKET_NAME = os.environ.get("CV_STORAGE_BUCKET", "candidate-cvs")

_ENDPOINT = os.environ.get("CV_STORAGE_ENDPOINT")
_ACCESS_KEY = os.environ.get("CV_STORAGE_ACCESS_KEY")
_SECRET_KEY = os.environ.get("CV_STORAGE_SECRET_KEY")

_client: Minio | None = None
if _ENDPOINT and _ACCESS_KEY and _SECRET_KEY:
    _client = Minio(_ENDPOINT, access_key=_ACCESS_KEY, secret_key=_SECRET_KEY, secure=False)


class StorageUnavailableError(RuntimeError):
    """Raised when object storage isn't configured or isn't reachable —
    unlike the project's Redis integrations, this is NOT fail-open: a CV
    upload with nowhere to put the file is a real failure the caller must
    see (surfaced as 503 by the route), not a silently dropped feature.
    """


def object_key(candidate_id: int) -> str:
    return f"candidates/{candidate_id}/cv.pdf"


def validate_cv_upload(content_type: str | None, size: int, header_bytes: bytes) -> None:
    """Pure validation, no I/O — unit tested directly. Checks both the
    declared Content-Type AND the actual PDF magic bytes, since a client
    can claim any content type for a request body.
    """
    if content_type != ALLOWED_CONTENT_TYPE:
        raise ValueError(f"Only {ALLOWED_CONTENT_TYPE} is accepted, got {content_type!r}")
    if size > MAX_CV_SIZE_BYTES:
        raise ValueError(f"File too large: {size} bytes exceeds the {MAX_CV_SIZE_BYTES}-byte cap")
    if not header_bytes.startswith(PDF_MAGIC_BYTES):
        raise ValueError("File content is not a valid PDF (missing %PDF- header)")


def upload_cv(candidate_id: int, data: bytes) -> str:
    if _client is None:
        raise StorageUnavailableError("CV_STORAGE_* environment variables are not configured")
    key = object_key(candidate_id)
    try:
        if not _client.bucket_exists(BUCKET_NAME):
            _client.make_bucket(BUCKET_NAME)
        _client.put_object(
            BUCKET_NAME, key, io.BytesIO(data), length=len(data), content_type=ALLOWED_CONTENT_TYPE,
        )
    except S3Error as exc:
        logger.error("CV upload to object storage failed for candidate %s", candidate_id, exc_info=True)
        raise StorageUnavailableError(str(exc)) from exc
    return key


def download_cv(candidate_id: int) -> bytes:
    if _client is None:
        raise StorageUnavailableError("CV_STORAGE_* environment variables are not configured")
    key = object_key(candidate_id)
    try:
        response = _client.get_object(BUCKET_NAME, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    except S3Error as exc:
        logger.error("CV download from object storage failed for candidate %s", candidate_id, exc_info=True)
        raise StorageUnavailableError(str(exc)) from exc
