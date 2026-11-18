"""Pure OIDC helper functions used by streamlit_app.py's login gate.

Kept in their own module, separate from streamlit_app.py, because that file
runs top-to-bottom as a Streamlit script on import (including `_require_login()`
and bare `st.session_state` access) — importing from it directly outside a
real `streamlit run` fails. These functions have no Streamlit dependency, so
they're importable and unit-testable on their own.

Both login and registration are now inline forms on the main page (no
redirect to a separate Keycloak-hosted page): login via `password_grant_login`
below, registration via `app/frontend/keycloak_admin.py`'s `create_user` +
this same `password_grant_login`. There is no Authorization Code flow left
in this app.
"""
import base64
import json

import requests

from app.common.config import settings

OIDC_CLIENT_ID = settings.oidc_client_id
OIDC_INTERNAL_ISSUER = settings.oidc_internal_issuer


def password_grant_login(username: str, password: str) -> dict:
    """Logs a user in via OAuth2 Resource Owner Password grant against
    `OIDC_INTERNAL_ISSUER`'s token endpoint.

    The app itself collects the username/password (text_input fields on the
    main page, see streamlit_app.py's `_require_login`) and exchanges them
    directly for tokens — no redirect to Keycloak's own hosted login page.
    OAuth2/Keycloak discourage this pattern for browser apps (the app sees
    the raw password, instead of never seeing it); accepted here as a known
    tradeoff so the login form renders on the same page under the app's own
    heading, not a separate window. See
    docs/architecture/ingress-and-identity.md Known Limitations.

    Args:
        username: the account's username (this realm uses the email address
            as username, set at registration — see `create_user` below).
        password: the account's plaintext password, sent once over this
            single request and not retained by this function.

    Returns:
        dict: the token endpoint's JSON response — at minimum
            `access_token`, `refresh_token`, `id_token`, `expires_in`.

    Raises:
        ValueError: if the credentials are rejected (any non-200 response).
    """
    token_resp = requests.post(
        f"{OIDC_INTERNAL_ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": OIDC_CLIENT_ID,
            "username": username,
            "password": password,
            "scope": "openid",
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        raise ValueError("Invalid username or password.")
    return token_resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    """Exchanges a refresh token for a new access/refresh token pair.

    Keycloak access tokens are short-lived, so `app/frontend/auth_helpers.py`
    calls this on a 401 to keep a long demo session working without a
    re-login (issue #26).

    Args:
        refresh_token: a refresh token previously issued by
            `password_grant_login` (or a prior call to this function).

    Returns:
        dict: the token endpoint's JSON response, same shape as
            `password_grant_login`'s.

    Raises:
        ValueError: if the refresh token itself has expired or is invalid —
            the caller has no recourse but to send the user back to the
            login screen.
    """
    token_resp = requests.post(
        f"{OIDC_INTERNAL_ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "refresh_token",
            "client_id": OIDC_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        raise ValueError("Session expired, please log in again.")
    return token_resp.json()


def decode_id_token(id_token: str) -> dict:
    """Decodes a JWT's payload (middle segment) without verifying its
    signature — an accepted gap for a local demo frontend; real signature
    verification happens server-side in `app/api/jwt_auth.py`. See
    streamlit_app.py's `_require_login` docstring for the full rationale.

    Args:
        id_token: a compact-serialized JWT (`header.payload.signature`).

    Returns:
        dict: the decoded payload claims (e.g. `sub`, `email`, `name`).
    """
    payload_b64 = id_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))
