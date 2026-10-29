"""Shared FastAPI dependencies: DB session lifecycle and API-key auth.

Auth approach (documented, not hidden): a single static API key compared via
a timing-safe check, not OAuth2/JWT. This is the honest MVP for a
single-tenant portfolio project per the brief's requirement ("authN/authZ on
all non-health endpoints before Phase 6 ships") without inventing a user
model, roles, or a token issuer that nothing in this project needs yet. See
docs/security-and-threat-model.md for what this does and doesn't protect
against, and the upgrade path.
"""
import os
import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.db import SessionLocal

# Same pattern as the dev Postgres credentials in docker-compose.yml: a
# documented local-only placeholder, not a secret meant to survive beyond a
# single-user dev machine. Must be overridden via env before any shared
# deployment (see assumption A7 in docs/assumptions-register.md).
_DEFAULT_DEV_API_KEY = "dev-local-key-change-me"

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(provided: str | None = Security(_api_key_header)) -> None:
    expected = os.environ.get("JME_API_KEY", _DEFAULT_DEV_API_KEY)
    if provided is None or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header",
        )


DbSession = Depends(get_db)
RequireApiKey = Depends(require_api_key)
