"""Thin client for the Keycloak Admin REST API operations the frontend
needs: creating a user directly (inline registration form, no redirect to
Keycloak's own hosted signup page) and assigning a real `candidate`/
`employer` realm role to a user after registration (issue #14).

Kept separate from streamlit_app.py and oidc.py — same "pure, Streamlit-free,
unit-testable" split used for the login flow — because this uses a different
credential (the `jme-admin` service account, least-privilege: manage-users/
view-users only) from the end-user OIDC flow in oidc.py.
"""
import requests

from app.common.config import settings

OIDC_INTERNAL_ISSUER = settings.oidc_internal_issuer
_REALM_BASE = OIDC_INTERNAL_ISSUER.rsplit("/realms/", 1)[0]
_REALM_NAME = OIDC_INTERNAL_ISSUER.rsplit("/realms/", 1)[1]

ADMIN_CLIENT_ID = settings.keycloak_admin_client_id
ADMIN_CLIENT_SECRET = settings.keycloak_admin_client_secret


def get_admin_token() -> str:
    """Client-credentials grant against the `jme-admin` service account
    (least-privilege: `manage-users`/`view-users` only).

    Returns:
        str: a bearer access token to pass as `admin_token` to
            `create_user`/`assign_realm_role` below.

    Raises:
        requests.HTTPError: if the client-credentials grant itself fails
            (e.g. a wrong/expired `ADMIN_CLIENT_SECRET`).
    """
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


def create_user(email: str, full_name: str, password: str, admin_token: str) -> str:
    """Creates a Keycloak user directly via the Admin API.

    The inline registration form posts straight here instead of redirecting
    to Keycloak's own hosted registration page. Username is set to the
    email address (this realm never asks for a separate username anywhere
    else).

    Args:
        email: the new account's email, also used as the Keycloak username.
        full_name: split on the first space into `firstName`/`lastName` —
            a single-field "full name" input, not two separate form fields.
        password: the new account's initial password (not temporary — the
            user is not forced to change it on first login).
        admin_token: a bearer token from `get_admin_token()`.

    Returns:
        str: the new user's Keycloak id (UUID) — the same value that later
            appears as the ID token's `sub` claim.

    Raises:
        ValueError: if the email is already registered (409 from Keycloak).
        requests.HTTPError: on any other non-2xx response.
    """
    first_name, _, last_name = full_name.partition(" ")
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = requests.post(
        f"{_REALM_BASE}/admin/realms/{_REALM_NAME}/users",
        headers=headers,
        json={
            "username": email,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": True,
            "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        },
        timeout=10,
    )
    if resp.status_code == 409:
        raise ValueError("An account with that email already exists.")
    resp.raise_for_status()
    return resp.headers["Location"].rsplit("/", 1)[-1]


def assign_realm_role(user_id: str, role: str, admin_token: str) -> None:
    """Assigns an existing realm role (e.g. `candidate`/`employer`) to a user.

    Args:
        user_id: the Keycloak user UUID — the ID token's `sub` claim already
            is this value, so callers don't need a separate username lookup.
        role: the realm role's name, as configured in Keycloak (not a
            client role).
        admin_token: a bearer token from `get_admin_token()`.

    Raises:
        requests.HTTPError: if the role doesn't exist or the assignment
            call fails.
    """
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
