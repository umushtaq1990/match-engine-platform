"""Thin client for app/api/job_routes.py (issue #22) -- same split rationale
as candidate_client.py: pure, Streamlit-free, unit-testable, talking to the
matching API over the Gateway with JME_API_KEY.
"""
import os

import requests

API_URL = os.environ.get("API_URL", "http://gateway/matching")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
_HEADERS = {"X-API-Key": API_KEY}


def list_employer_jobs(keycloak_user_id: str) -> list[dict]:
    resp = requests.get(f"{API_URL}/employers/{keycloak_user_id}/jobs", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_job_details(job_id: int) -> dict:
    resp = requests.get(f"{API_URL}/jobs/{job_id}/details", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_job(
    posted_by_keycloak_user_id: str, title: str, company_name: str, description: str,
    location_city: str, location_region: str, salary_min: int | None, salary_max: int | None,
    primary_occupation_id: int | None, experience_level: str | None,
    required_skill_ids: list[int], preferred_skill_ids: list[int],
) -> dict:
    resp = requests.post(
        f"{API_URL}/jobs",
        headers=_HEADERS,
        json={
            "posted_by_keycloak_user_id": posted_by_keycloak_user_id,
            "title": title, "company_name": company_name, "description": description,
            "location_city": location_city, "location_region": location_region,
            "salary_min": salary_min, "salary_max": salary_max,
            "primary_occupation_id": primary_occupation_id, "experience_level": experience_level,
            "required_skill_ids": required_skill_ids, "preferred_skill_ids": preferred_skill_ids,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def update_job_details(
    job_id: int, title: str, company_name: str, description: str,
    location_city: str, location_region: str, salary_min: int | None, salary_max: int | None,
    primary_occupation_id: int | None, experience_level: str | None,
    required_skill_ids: list[int], preferred_skill_ids: list[int],
) -> dict:
    resp = requests.patch(
        f"{API_URL}/jobs/{job_id}/details",
        headers=_HEADERS,
        json={
            "title": title, "company_name": company_name, "description": description,
            "location_city": location_city, "location_region": location_region,
            "salary_min": salary_min, "salary_max": salary_max,
            "primary_occupation_id": primary_occupation_id, "experience_level": experience_level,
            "required_skill_ids": required_skill_ids, "preferred_skill_ids": preferred_skill_ids,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
