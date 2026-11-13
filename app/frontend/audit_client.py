"""Thin client for app/api/audit_routes.py (issue #24) -- same split
rationale as candidate_client.py/job_client.py. Issue #26: also takes a
verified access_token, sent as a bearer token -- this endpoint is
strict-ownership, only the entity's own owner may read its history.
"""
import os

import requests

API_URL = os.environ.get("API_URL", "http://gateway/matching")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
_HEADERS = {"X-API-Key": API_KEY}


def get_change_log(entity_type: str, entity_id: int, access_token: str) -> list[dict]:
    headers = {**_HEADERS, "Authorization": f"Bearer {access_token}"}
    resp = requests.get(f"{API_URL}/audit/{entity_type}/{entity_id}", headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()
