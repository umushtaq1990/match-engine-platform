"""Shared FastAPI dependencies for the extraction service: DB session +
API-key auth. Deliberately duplicated from app/api/deps.py and
app/analytics/deps.py rather than imported — same service-boundary
reasoning as Phase 14's analytics service (docs/behavioral-analytics.md):
each service's image stays independent of the others' dependencies. Same
JME_API_KEY reused across all three services for v1 (all internal-only
today).
"""
import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.common.config import settings
from app.db import SessionLocal

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_db():
    """FastAPI dependency: yields a `Session`, closing it when the request ends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(provided: str | None = Security(_api_key_header)) -> None:
    """FastAPI dependency: raises 401 unless `provided` matches `settings.jme_api_key`.

    Args:
        provided: the raw `X-API-Key` header value, or `None` if absent.

    Raises:
        fastapi.HTTPException: 401 on a missing/incorrect key.
    """
    expected = settings.jme_api_key
    if provided is None or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header",
        )


DbSession = Depends(get_db)
RequireApiKey = Depends(require_api_key)
