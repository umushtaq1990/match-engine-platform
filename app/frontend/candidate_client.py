"""Thin client for app/api/profile_routes.py's candidate-details/occupations/
skills endpoints (issue #18) — same split rationale as profile_client.py:
pure, Streamlit-free, unit-testable, talking to the matching API over the
Gateway with JME_API_KEY.
"""
import os

import requests

API_URL = os.environ.get("API_URL", "http://gateway/matching")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
_HEADERS = {"X-API-Key": API_KEY}


def get_candidate_details(candidate_id: int, access_token: str) -> dict:
    headers = {**_HEADERS, "Authorization": f"Bearer {access_token}"}
    resp = requests.get(f"{API_URL}/candidates/{candidate_id}/details", headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def update_candidate_details(
    candidate_id: int, location_city: str, postal_code: str | None, country: str | None,
    desired_salary_min: int | None, desired_salary_max: int | None,
    primary_occupation_id: int | None, experience_level: str | None, skill_ids: list[int],
    access_token: str,
) -> dict:
    headers = {**_HEADERS, "Authorization": f"Bearer {access_token}"}
    resp = requests.patch(
        f"{API_URL}/candidates/{candidate_id}/details",
        headers=headers,
        json={
            "location_city": location_city,
            "postal_code": postal_code,
            "country": country,
            "desired_salary_min": desired_salary_min,
            "desired_salary_max": desired_salary_max,
            "primary_occupation_id": primary_occupation_id,
            "experience_level": experience_level,
            "skill_ids": skill_ids,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def list_occupations() -> list[dict]:
    resp = requests.get(f"{API_URL}/occupations", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_occupation(title: str) -> dict:
    resp = requests.post(f"{API_URL}/occupations", headers=_HEADERS, json={"title": title}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def list_skills() -> list[dict]:
    resp = requests.get(f"{API_URL}/skills", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_skill(name: str) -> dict:
    resp = requests.post(f"{API_URL}/skills", headers=_HEADERS, json={"name": name}, timeout=10)
    resp.raise_for_status()
    return resp.json()
