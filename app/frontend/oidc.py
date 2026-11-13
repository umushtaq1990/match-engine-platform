"""Pure OIDC helper functions used by streamlit_app.py's login gate.

Kept in their own module, separate from streamlit_app.py, because that file
runs top-to-bottom as a Streamlit script on import (including `_require_login()`
and bare `st.session_state` access) — importing from it directly outside a
real `streamlit run` fails. These functions have no Streamlit dependency, so
they're importable and unit-testable on their own.
"""
import base64
import json
import os

import requests

OIDC_CLIENT_ID = "jme-frontend"
OIDC_PUBLIC_ISSUER = os.environ.get("OIDC_PUBLIC_ISSUER", "http://localhost:8180/realms/jme")
OIDC_INTERNAL_ISSUER = os.environ.get("OIDC_INTERNAL_ISSUER", "http://keycloak:8080/realms/jme")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "http://localhost:8080/")


def is_state_valid(returned_state: str | None, pending: dict[str, float]) -> bool:
    return returned_state in pending


def build_registration_url(state: str) -> str:
    """Same params as build_auth_url, against Keycloak's registration endpoint
    instead of its login endpoint — redirects back through the exact same
    `?code=&state=` callback _require_login() already handles (issue #14)."""
    return (
        f"{OIDC_PUBLIC_ISSUER}/protocol/openid-connect/registrations"
        f"?client_id={OIDC_CLIENT_ID}&response_type=code&scope=openid"
        f"&redirect_uri={OIDC_REDIRECT_URI}&state={state}"
    )


def exchange_code_for_token(code: str) -> dict:
    """POSTs to the Keycloak token endpoint. Raises ValueError on a non-200
    response rather than returning one, so callers can't forget to check."""
    token_resp = requests.post(
        f"{OIDC_INTERNAL_ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": OIDC_REDIRECT_URI,
            "client_id": OIDC_CLIENT_ID,
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        raise ValueError(f"Login failed: {token_resp.text}")
    return token_resp.json()


def password_grant_login(username: str, password: str) -> dict:
    """Resource Owner Password grant: the app itself collects the username/
    password (text_input fields on the main page, see streamlit_app.py's
    _require_login) and exchanges them directly for tokens — no redirect to
    Keycloak's own hosted login page. OAuth2/Keycloak discourage this
    pattern for browser apps (the app sees the raw password, instead of
    never seeing it); accepted here as a known tradeoff so the login form
    renders on the same page under the app's own heading, not a separate
    window. See docs/architecture/ingress-and-identity.md Known
    Limitations. Raises ValueError on a non-200 response.
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


def decode_id_token(id_token: str) -> dict:
    """Decodes the JWT payload without verifying its signature — accepted gap
    for a local demo, see streamlit_app.py's _require_login docstring."""
    payload_b64 = id_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))
