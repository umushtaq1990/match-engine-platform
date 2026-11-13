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


def get_job_details(job_id: int, viewed_by_candidate_id: int | None = None) -> dict:
    params = {"viewed_by_candidate_id": viewed_by_candidate_id} if viewed_by_candidate_id is not None else None
    resp = requests.get(f"{API_URL}/jobs/{job_id}/details", headers=_HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_job(
    posted_by_keycloak_user_id: str, title: str, company_name: str, description: str,
    location_city: str, postal_code: str | None, country: str | None,
    salary_min: int | None, salary_max: int | None,
    primary_occupation_id: int | None, experience_level: str | None,
    required_skill_ids: list[int], preferred_skill_ids: list[int],
) -> dict:
    resp = requests.post(
        f"{API_URL}/jobs",
        headers=_HEADERS,
        json={
            "posted_by_keycloak_user_id": posted_by_keycloak_user_id,
            "title": title, "company_name": company_name, "description": description,
            "location_city": location_city, "postal_code": postal_code, "country": country,
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
    location_city: str, postal_code: str | None, country: str | None,
    salary_min: int | None, salary_max: int | None,
    primary_occupation_id: int | None, experience_level: str | None,
    required_skill_ids: list[int], preferred_skill_ids: list[int],
    changed_by_keycloak_user_id: str | None = None,
) -> dict:
    resp = requests.patch(
        f"{API_URL}/jobs/{job_id}/details",
        headers=_HEADERS,
        json={
            "title": title, "company_name": company_name, "description": description,
            "location_city": location_city, "postal_code": postal_code, "country": country,
            "salary_min": salary_min, "salary_max": salary_max,
            "primary_occupation_id": primary_occupation_id, "experience_level": experience_level,
            "required_skill_ids": required_skill_ids, "preferred_skill_ids": preferred_skill_ids,
            "changed_by_keycloak_user_id": changed_by_keycloak_user_id,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def apply_to_job(candidate_id: int, job_id: int) -> dict:
    resp = requests.post(f"{API_URL}/candidates/{candidate_id}/jobs/{job_id}/apply", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def save_job(candidate_id: int, job_id: int) -> dict:
    resp = requests.post(f"{API_URL}/candidates/{candidate_id}/jobs/{job_id}/save", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_job_interactions(candidate_id: int) -> list[dict]:
    resp = requests.get(f"{API_URL}/candidates/{candidate_id}/job-interactions", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()
