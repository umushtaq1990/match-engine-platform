"""Shared FastAPI dependencies: DB session lifecycle and API-key auth.

Auth approach (documented, not hidden): a single static API key compared via
a timing-safe check, not OAuth2/JWT. This is the honest MVP for a
single-tenant portfolio project per the brief's requirement ("authN/authZ on
all non-health endpoints before Phase 6 ships") without inventing a user
model, roles, or a token issuer that nothing in this project needs yet. See
docs/security-and-threat-model.md for what this does and doesn't protect
against, and the upgrade path.
"""
import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.common.config import settings
from app.db import SessionLocal

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_db():
    """FastAPI dependency: yields a `Session`, closing it when the request
    ends regardless of success or failure.

    Yields:
        sqlalchemy.orm.Session: a fresh session bound to this process's
            engine (see app/db.py). Never returns — generator-style FastAPI
            dependency.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(provided: str | None = Security(_api_key_header)) -> None:
    """FastAPI dependency: raises 401 unless the `X-API-Key` header matches
    `settings.jme_api_key`.

    Args:
        provided: the raw `X-API-Key` header value, or `None` if absent —
            injected by FastAPI via `_api_key_header`, never passed by
            calling code directly.

    Raises:
        fastapi.HTTPException: 401 if the header is missing or doesn't
            match, via a timing-safe comparison (`secrets.compare_digest`)
            so response latency can't be used to brute-force the key.
    """
    expected = settings.jme_api_key
    if provided is None or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header",
        )


DbSession = Depends(get_db)
RequireApiKey = Depends(require_api_key)
