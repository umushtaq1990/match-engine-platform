"""Real verification of the Keycloak access token (issue #26) -- until now
every route trusted a client-supplied `keycloak_user_id`/`candidate_id`/
`job_id` with zero check that the caller actually holds a valid token for
that identity. `jme-frontend` is a public Keycloak client (no client
secret, see docker/keycloak/jme-realm.json), so verification is via JWKS/
RS256 signature check, not a shared secret.

Used alongside RequireApiKey (app/api/deps.py), not instead of it -- the
API key still gates "is this even a client of this service at all" the
same way it always has; this adds "is the caller who they claim to be."
"""
import os
import time

import httpx
from fastapi import Header, HTTPException, status
from jose import jwt
from jose.exceptions import JOSEError

OIDC_CLIENT_ID = "jme-frontend"
OIDC_INTERNAL_ISSUER = os.environ.get("OIDC_INTERNAL_ISSUER", "http://keycloak:8080/realms/jme")
_JWKS_URL = f"{OIDC_INTERNAL_ISSUER}/protocol/openid-connect/certs"
_JWKS_CACHE_SECONDS = 300

_jwks_cache: dict | None = None
_jwks_cached_at: float = 0.0


def _fetch_jwks(force: bool = False) -> dict:
    global _jwks_cache, _jwks_cached_at
    if not force and _jwks_cache is not None and (time.time() - _jwks_cached_at) < _JWKS_CACHE_SECONDS:
        return _jwks_cache
    resp = httpx.get(_JWKS_URL, timeout=5.0)
    resp.raise_for_status()
    _jwks_cache = resp.json()
    _jwks_cached_at = time.time()
    return _jwks_cache


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def get_verified_claims(authorization: str | None = Header(default=None)) -> dict:
    """Checks the header is present *before* ever touching the network (JWKS
    fetch) -- keeps a request with no Authorization header failing fast and
    offline, same as require_api_key's missing-header check.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise _unauthorized("Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()

    unverified_header = jwt.get_unverified_header(token)
    jwks = _fetch_jwks()
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == unverified_header.get("kid")), None)
    if key is None:
        jwks = _fetch_jwks(force=True)
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == unverified_header.get("kid")), None)
    if key is None:
        raise _unauthorized("Unknown signing key")

    try:
        claims = jwt.decode(
            token, key, algorithms=["RS256"], issuer=OIDC_INTERNAL_ISSUER,
            # Keycloak's default access token doesn't set `aud` to this
            # specific client -- `azp` (authorized party) is the field that
            # actually identifies which client the token was issued to,
            # checked explicitly below instead.
            options={"verify_aud": False},
        )
    except JOSEError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc

    if claims.get("azp") != OIDC_CLIENT_ID:
        raise _unauthorized("Token was not issued to this client")

    return claims
