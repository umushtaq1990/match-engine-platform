"""Thin client for app/api/profile_routes.py (issue #15) — the app's own
user_profiles table, not Keycloak. Kept separate from streamlit_app.py,
same "pure, Streamlit-free, unit-testable" split used for oidc.py and
keycloak_admin.py: this talks to a different backend (the matching API,
over the Gateway) with a different credential (JME_API_KEY) than either of
those two Keycloak clients.
"""
import os

import requests

API_URL = os.environ.get("API_URL", "http://gateway/matching")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
_HEADERS = {"X-API-Key": API_KEY}


def get_profile(keycloak_user_id: str) -> dict | None:
    resp = requests.get(f"{API_URL}/profiles/{keycloak_user_id}", headers=_HEADERS, timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def create_profile(
    keycloak_user_id: str, role: str, full_name: str, email: str, company_name: str | None = None,
) -> dict:
    resp = requests.post(
        f"{API_URL}/profiles",
        headers=_HEADERS,
        json={
            "keycloak_user_id": keycloak_user_id,
            "role": role,
            "full_name": full_name,
            "email": email,
            "company_name": company_name,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
