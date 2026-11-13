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
import os

import requests

OIDC_CLIENT_ID = "jme-frontend"
OIDC_INTERNAL_ISSUER = os.environ.get("OIDC_INTERNAL_ISSUER", "http://keycloak:8080/realms/jme")


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
