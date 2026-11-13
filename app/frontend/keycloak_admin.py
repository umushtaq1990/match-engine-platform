"""Thin client for the one Keycloak Admin REST API operation the frontend
needs: assigning a real `candidate`/`employer` realm role to a user after
self-registration (issue #14).

Kept separate from streamlit_app.py and oidc.py — same "pure, Streamlit-free,
unit-testable" split used for the login flow — because this uses a different
credential (the `jme-admin` service account, least-privilege: manage-users/
view-users only) from the end-user OIDC flow in oidc.py.
"""
import os

import requests

OIDC_INTERNAL_ISSUER = os.environ.get("OIDC_INTERNAL_ISSUER", "http://keycloak:8080/realms/jme")
_REALM_BASE = OIDC_INTERNAL_ISSUER.rsplit("/realms/", 1)[0]
_REALM_NAME = OIDC_INTERNAL_ISSUER.rsplit("/realms/", 1)[1]

ADMIN_CLIENT_ID = os.environ.get("KEYCLOAK_ADMIN_CLIENT_ID", "jme-admin")
ADMIN_CLIENT_SECRET = os.environ.get("KEYCLOAK_ADMIN_CLIENT_SECRET", "")


def get_admin_token() -> str:
    """Client-credentials grant against the jme-admin service account."""
    resp = requests.post(
        f"{OIDC_INTERNAL_ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": ADMIN_CLIENT_ID,
            "client_secret": ADMIN_CLIENT_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def assign_realm_role(user_id: str, role: str, admin_token: str) -> None:
    """user_id is the Keycloak user UUID — the ID token's `sub` claim already
    is this value, so callers don't need a separate username lookup."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    role_resp = requests.get(
        f"{_REALM_BASE}/admin/realms/{_REALM_NAME}/roles/{role}",
        headers=headers,
        timeout=10,
    )
    role_resp.raise_for_status()
    role_repr = role_resp.json()

    assign_resp = requests.post(
        f"{_REALM_BASE}/admin/realms/{_REALM_NAME}/users/{user_id}/role-mappings/realm",
        headers=headers,
        json=[{"id": role_repr["id"], "name": role_repr["name"]}],
        timeout=10,
    )
    assign_resp.raise_for_status()
