"""Thin client for app/api/job_routes.py (issue #22) -- same split rationale
as candidate_client.py: pure, Streamlit-free, unit-testable, talking to the
matching API over the Gateway with JME_API_KEY. Issue #26: every function
also takes a verified access_token, sent as a bearer token -- identity
comes from that token server-side now, not a client-supplied
posted_by_keycloak_user_id/changed_by_keycloak_user_id field.
"""
import os

import requests

API_URL = os.environ.get("API_URL", "http://gateway/matching")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
_HEADERS = {"X-API-Key": API_KEY}


def _auth_headers(access_token: str) -> dict:
    return {**_HEADERS, "Authorization": f"Bearer {access_token}"}


def list_employer_jobs(keycloak_user_id: str, access_token: str) -> list[dict]:
    resp = requests.get(
        f"{API_URL}/employers/{keycloak_user_id}/jobs", headers=_auth_headers(access_token), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_job_details(job_id: int, access_token: str, viewed_by_candidate_id: int | None = None) -> dict:
    params = {"viewed_by_candidate_id": viewed_by_candidate_id} if viewed_by_candidate_id is not None else None
    resp = requests.get(
        f"{API_URL}/jobs/{job_id}/details", headers=_auth_headers(access_token), params=params, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def create_job(
    title: str, company_name: str, description: str,
    location_city: str, postal_code: str | None, country: str | None,
    salary_min: int | None, salary_max: int | None,
    primary_occupation_id: int | None, experience_level: str | None,
    required_skill_ids: list[int], preferred_skill_ids: list[int],
    access_token: str,
) -> dict:
    resp = requests.post(
        f"{API_URL}/jobs",
        headers=_auth_headers(access_token),
        json={
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
    access_token: str,
) -> dict:
    resp = requests.patch(
        f"{API_URL}/jobs/{job_id}/details",
        headers=_auth_headers(access_token),
        json={
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


def apply_to_job(candidate_id: int, job_id: int, access_token: str) -> dict:
    resp = requests.post(
        f"{API_URL}/candidates/{candidate_id}/jobs/{job_id}/apply",
        headers=_auth_headers(access_token), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def save_job(candidate_id: int, job_id: int, access_token: str) -> dict:
    resp = requests.post(
        f"{API_URL}/candidates/{candidate_id}/jobs/{job_id}/save",
        headers=_auth_headers(access_token), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_job_interactions(candidate_id: int, access_token: str) -> list[dict]:
    resp = requests.get(
        f"{API_URL}/candidates/{candidate_id}/job-interactions",
        headers=_auth_headers(access_token), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
