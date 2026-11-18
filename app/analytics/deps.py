"""Shared FastAPI dependencies for the analytics service: DB session + API-key auth.

Deliberately duplicated from app/api/deps.py rather than imported: the whole
point of Phase 14's service-boundary decision (docs/behavioral-analytics.md)
is that the analytics image never imports from the matching engine's app.api
package, so docker/Dockerfile.analytics stays independent of app/api's
dependencies. ~20 duplicated lines is a trade worth making for that
independence. The same key (JME_API_KEY) is reused across both services for
v1 — both are internal-only today, the same trust boundary; a separate key
per service is a reasonable future hardening step, not needed yet.
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
