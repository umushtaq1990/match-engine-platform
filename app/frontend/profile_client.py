"""Thin client for app/api/profile_routes.py (issue #15) — the app's own
user_profiles table, not Keycloak. Kept separate from streamlit_app.py,
same "pure, Streamlit-free, unit-testable" split used for oidc.py and
keycloak_admin.py: this talks to a different backend (the matching API,
over the Gateway) with a different credential (JME_API_KEY) than either of
those two Keycloak clients. Issue #26: both functions also take a verified
access_token, sent as a bearer token -- identity comes from that token
server-side now, not a client-supplied keycloak_user_id body field.
"""
import os

import requests

API_URL = os.environ.get("API_URL", "http://gateway/matching")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
_HEADERS = {"X-API-Key": API_KEY}


def _auth_headers(access_token: str) -> dict:
    return {**_HEADERS, "Authorization": f"Bearer {access_token}"}


def get_profile(keycloak_user_id: str, access_token: str) -> dict | None:
    resp = requests.get(
        f"{API_URL}/profiles/{keycloak_user_id}", headers=_auth_headers(access_token), timeout=10,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def create_profile(
    role: str, full_name: str, email: str, access_token: str, company_name: str | None = None,
) -> dict:
    resp = requests.post(
        f"{API_URL}/profiles",
        headers=_auth_headers(access_token),
        json={
            "role": role,
            "full_name": full_name,
            "email": email,
            "company_name": company_name,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
