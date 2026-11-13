"""Thin client for app/api/audit_routes.py (issue #24) -- same split
rationale as candidate_client.py/job_client.py.
"""
import os

import requests

API_URL = os.environ.get("API_URL", "http://gateway/matching")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
_HEADERS = {"X-API-Key": API_KEY}


def get_change_log(entity_type: str, entity_id: int) -> list[dict]:
    resp = requests.get(f"{API_URL}/audit/{entity_type}/{entity_id}", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()
