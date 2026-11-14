"""DB-touching tests for app/api/job_routes.py (issue #22). Uses
matching_db_session (tests/conftest.py) since these routes read/write
Job/JobSkill, same real-transaction-wrapped pattern as
tests/api/test_routes_db.py.

Issue #26: identity comes from a verified JWT, not a client-supplied
`posted_by_keycloak_user_id`/`changed_by_keycloak_user_id` field (both
removed from the request schemas) -- tests override `get_verified_claims`
the same way they already override `get_db`. PATCH/apply/save/
job-interactions are strict-ownership, so those tests must make the
client's overridden `sub` match the job's `posted_by_keycloak_user_id` or
a UserProfile linking the candidate to that `sub` (see _make_owned_candidate).
GET /jobs/{id}/details has no ownership check at all (any authenticated
caller may view any job's details, like a real job posting).
"""
import datetime as dt
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.jwt_auth import get_verified_claims
from app.api.main import app
from app.identity.models import UserProfile, UserRole
from app.ingestion.source_models import Candidate, EmploymentType, Job, Occupation, Skill

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _client_bound_to(session, sub: str = "employer-1") -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    app.dependency_overrides[get_verified_claims] = lambda: {"sub": sub}
    return TestClient(app)


def _make_job(session, posted_by="employer-1", title="Existing Job") -> Job:
    job = Job(
        title=title, company_name="Acme Co", description_raw="desc",
        location_city="Austin", location_region="TX",
        employment_type=EmploymentType.full_time, posted_at=dt.datetime.now(dt.timezone.utc),
        posted_by_keycloak_user_id=posted_by,
    )
    session.add(job)
    session.flush()
    return job


def _make_candidate(session, email="interactions@example-synthetic.test") -> Candidate:
    candidate = Candidate(
        full_name="Interactions Candidate", email=email, location_city="Austin",
        location_region="TX", years_experience=3, created_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(candidate)
    session.flush()
    return candidate


def _make_owned_candidate(session, sub: str, email="owned-issue26@example-synthetic.test") -> Candidate:
    candidate = _make_candidate(session, email=email)
    session.add(UserProfile(
        keycloak_user_id=sub, role=UserRole.candidate, full_name="Owned Candidate",
        email=email, candidate_id=candidate.id,
    ))
    session.flush()
    return candidate


def test_apply_to_job_creates_interaction(matching_db_session):
    candidate = _make_owned_candidate(matching_db_session, sub="candidate-apply-1")
    job = _make_job(matching_db_session)
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="candidate-apply-1")
    try:
        response = client.post(
            f"/candidates/{candidate.id}/jobs/{job.id}/apply", headers=API_KEY_HEADERS,
        )
        assert response.status_code == 201

        interactions = client.get(f"/candidates/{candidate.id}/job-interactions", headers=API_KEY_HEADERS)
        assert interactions.status_code == 200
        body = interactions.json()
        assert len(body) == 1
        assert body[0]["job_id"] == job.id
        assert body[0]["interaction_type"] == "applied"
        assert body[0]["title"] == job.title
    finally:
        app.dependency_overrides.clear()


def test_apply_to_job_is_idempotent_on_repeat_click(matching_db_session):
    candidate = _make_owned_candidate(matching_db_session, sub="candidate-apply-2")
    job = _make_job(matching_db_session)
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="candidate-apply-2")
    try:
        client.post(f"/candidates/{candidate.id}/jobs/{job.id}/apply", headers=API_KEY_HEADERS)
        second = client.post(f"/candidates/{candidate.id}/jobs/{job.id}/apply", headers=API_KEY_HEADERS)
        assert second.status_code == 201

        interactions = client.get(f"/candidates/{candidate.id}/job-interactions", headers=API_KEY_HEADERS)
        assert len(interactions.json()) == 1
    finally:
        app.dependency_overrides.clear()


def test_save_job_creates_interaction_distinct_from_apply(matching_db_session):
    candidate = _make_owned_candidate(matching_db_session, sub="candidate-save-1")
    job = _make_job(matching_db_session)
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="candidate-save-1")
    try:
        client.post(f"/candidates/{candidate.id}/jobs/{job.id}/apply", headers=API_KEY_HEADERS)
        client.post(f"/candidates/{candidate.id}/jobs/{job.id}/save", headers=API_KEY_HEADERS)

        interactions = client.get(f"/candidates/{candidate.id}/job-interactions", headers=API_KEY_HEADERS)
        types = sorted(i["interaction_type"] for i in interactions.json())
        assert types == ["applied", "saved"]
    finally:
        app.dependency_overrides.clear()


def test_apply_to_job_rejects_a_candidate_id_the_caller_doesnt_own(matching_db_session):
    job = _make_job(matching_db_session)
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="candidate-impostor")
    try:
        response = client.post(f"/candidates/999999999/jobs/{job.id}/apply", headers=API_KEY_HEADERS)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_apply_to_job_returns_404_for_missing_job(matching_db_session):
    candidate = _make_owned_candidate(matching_db_session, sub="candidate-apply-3")
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="candidate-apply-3")
    try:
        response = client.post(
            f"/candidates/{candidate.id}/jobs/999999999/apply", headers=API_KEY_HEADERS,
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_create_job_records_audit_entry(matching_db_session):
    client = _client_bound_to(matching_db_session, sub="employer-audit")
    try:
        response = client.post(
            "/jobs", headers=API_KEY_HEADERS,
            json={
                "title": "Audited Job", "company_name": "Acme Co", "description": "desc",
                "location_city": "Austin",
            },
        )
        assert response.status_code == 201
        job_id = response.json()["id"]

        audit = client.get(f"/audit/job_details/{job_id}", headers=API_KEY_HEADERS)
        assert audit.status_code == 200
        assert len(audit.json()) == 1
        assert audit.json()[0]["changed_by_keycloak_user_id"] == "employer-audit"
    finally:
        app.dependency_overrides.clear()


def test_update_job_details_records_audit_entry_for_changed_fields_only(matching_db_session):
    job = _make_job(matching_db_session, posted_by="employer-update-audit")
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="employer-update-audit")
    try:
        client.patch(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
            json={
                "title": job.title, "company_name": job.company_name, "description": "desc",
                "location_city": "Dallas",
            },
        )
        audit = client.get(f"/audit/job_details/{job.id}", headers=API_KEY_HEADERS)
        assert audit.status_code == 200
        body = audit.json()
        assert len(body) == 1
        assert "location_city" in body[0]["changes"]
        assert "title" not in body[0]["changes"]
    finally:
        app.dependency_overrides.clear()


def test_create_job_creates_job_and_split_skills(matching_db_session):
    occupation = Occupation(code="ISSUE22-OCC", title="Issue22 Occupation")
    required_skill = Skill(name="Issue22 Required Skill")
    preferred_skill = Skill(name="Issue22 Preferred Skill")
    matching_db_session.add_all([occupation, required_skill, preferred_skill])
    matching_db_session.flush()
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="employer-abc")
    try:
        response = client.post(
            "/jobs", headers=API_KEY_HEADERS,
            json={
                "title": "Backend Engineer", "company_name": "Acme Co",
                "description": "Build things", "location_city": "Austin",
                "salary_min": 90000, "salary_max": 130000,
                "primary_occupation_id": occupation.id, "experience_level": "senior",
                "required_skill_ids": [required_skill.id], "preferred_skill_ids": [preferred_skill.id],
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Backend Engineer"
        assert body["experience_level"] == "senior"
        assert body["required_skill_ids"] == [required_skill.id]
        assert body["preferred_skill_ids"] == [preferred_skill.id]
    finally:
        app.dependency_overrides.clear()


def test_list_employer_jobs_returns_only_that_employers_jobs(matching_db_session):
    _make_job(matching_db_session, posted_by="employer-1", title="Job A")
    _make_job(matching_db_session, posted_by="employer-2", title="Job B")
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="employer-1")
    try:
        response = client.get("/employers/employer-1/jobs", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        titles = [j["title"] for j in response.json()]
        assert titles == ["Job A"]
    finally:
        app.dependency_overrides.clear()


def test_list_employer_jobs_rejects_a_caller_asking_for_someone_elses_list(matching_db_session):
    client = _client_bound_to(matching_db_session, sub="employer-someone-else")
    try:
        response = client.get("/employers/employer-1/jobs", headers=API_KEY_HEADERS)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_get_job_details_returns_404_for_missing_job(matching_db_session):
    client = _client_bound_to(matching_db_session)
    try:
        response = client.get("/jobs/999999999/details", headers=API_KEY_HEADERS)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_get_job_details_has_no_ownership_check(matching_db_session):
    """Issue #26: viewing a job posting is open to any authenticated caller,
    including one that isn't its owner -- unlike candidate-details, a job
    posting is meant to be seen by candidates evaluating it.
    """
    job = _make_job(matching_db_session, posted_by="employer-owner")
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="someone-else-entirely")
    try:
        response = client.get(f"/jobs/{job.id}/details", headers=API_KEY_HEADERS)
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_get_job_details_includes_occupation_title(matching_db_session):
    occupation = Occupation(code="ISSUE25-OCC", title="Issue25 Occupation")
    matching_db_session.add(occupation)
    matching_db_session.flush()
    job = _make_job(matching_db_session)
    job.occupation_id = occupation.id
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session)
    try:
        response = client.get(f"/jobs/{job.id}/details", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        assert response.json()["occupation_title"] == "Issue25 Occupation"
    finally:
        app.dependency_overrides.clear()


def test_get_job_details_fires_recommendation_clicked_only_when_viewed_by_candidate(matching_db_session):
    candidate = _make_owned_candidate(matching_db_session, sub="candidate-click-1")
    job = _make_job(matching_db_session)
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="candidate-click-1")
    try:
        with patch("app.api.job_routes.log_event") as mock_log_event:
            client.get(f"/jobs/{job.id}/details", headers=API_KEY_HEADERS)
            mock_log_event.assert_not_called()

            client.get(
                f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
                params={"viewed_by_candidate_id": candidate.id},
            )
            mock_log_event.assert_called_once_with(
                actor_type="candidate", actor_id=candidate.id,
                event_type="recommendation_clicked", entity_type="job", entity_id=job.id,
            )
    finally:
        app.dependency_overrides.clear()


def test_get_job_details_rejects_viewed_by_candidate_id_the_caller_doesnt_own(matching_db_session):
    job = _make_job(matching_db_session)
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="candidate-impostor")
    try:
        response = client.get(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS, params={"viewed_by_candidate_id": 999999999},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_updates_fields_and_replaces_skills(matching_db_session):
    job = _make_job(matching_db_session, posted_by="employer-patch-1")
    old_skill = Skill(name="Issue22 Old Skill")
    new_required = Skill(name="Issue22 New Required")
    new_preferred = Skill(name="Issue22 New Preferred")
    matching_db_session.add_all([old_skill, new_required, new_preferred])
    matching_db_session.flush()
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="employer-patch-1")
    try:
        response = client.patch(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
            json={
                "title": "Senior Backend Engineer", "company_name": "Acme Co",
                "description": "Updated desc", "location_city": "Dallas",
                "salary_min": 100000, "salary_max": 150000, "experience_level": "expert",
                "required_skill_ids": [new_required.id], "preferred_skill_ids": [new_preferred.id],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Senior Backend Engineer"
        assert body["location_city"] == "Dallas"
        assert body["experience_level"] == "expert"
        assert body["required_skill_ids"] == [new_required.id]
        assert body["preferred_skill_ids"] == [new_preferred.id]
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_rejects_a_caller_who_doesnt_own_it(matching_db_session):
    job = _make_job(matching_db_session, posted_by="employer-real-owner")
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="employer-impostor")
    try:
        response = client.patch(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
            json={"title": "X", "company_name": "Y", "description": "Z", "location_city": "Austin"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_sets_postal_code_and_country(matching_db_session):
    job = _make_job(matching_db_session, posted_by="employer-patch-2")
    matching_db_session.commit()
    client = _client_bound_to(matching_db_session, sub="employer-patch-2")
    try:
        response = client.patch(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
            json={
                "title": job.title, "company_name": job.company_name, "description": "desc",
                "location_city": "Austin", "postal_code": "78701", "country": "USA",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["postal_code"] == "78701"
        assert body["country"] == "USA"
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_does_not_overwrite_location_region(matching_db_session):
    job = _make_job(matching_db_session, posted_by="employer-patch-3")
    matching_db_session.commit()
    client = _client_bound_to(matching_db_session, sub="employer-patch-3")
    try:
        response = client.patch(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
            json={
                "title": job.title, "company_name": job.company_name,
                "description": "desc", "location_city": "Dallas",
            },
        )
        assert response.status_code == 200
        matching_db_session.refresh(job)
        assert job.location_region == "TX"
        assert job.location_city == "Dallas"
    finally:
        app.dependency_overrides.clear()


def test_create_job_defaults_location_region_to_unknown_placeholder(matching_db_session):
    client = _client_bound_to(matching_db_session, sub="employer-placeholder")
    try:
        response = client.post(
            "/jobs", headers=API_KEY_HEADERS,
            json={
                "title": "X", "company_name": "Y", "description": "Z", "location_city": "Austin",
            },
        )
        assert response.status_code == 201
        job = matching_db_session.get(Job, response.json()["id"])
        assert job.location_region == "Unknown"
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_returns_404_for_missing_job(matching_db_session):
    client = _client_bound_to(matching_db_session)
    try:
        response = client.patch(
            "/jobs/999999999/details", headers=API_KEY_HEADERS,
            json={
                "title": "X", "company_name": "Y", "description": "Z",
                "location_city": "Austin",
            },
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_patch_job_details_dedupes_a_skill_listed_as_both_required_and_preferred(matching_db_session):
    job = _make_job(matching_db_session, posted_by="employer-dedupe")
    shared_skill = Skill(name="Issue22 Shared Skill")
    matching_db_session.add(shared_skill)
    matching_db_session.flush()
    matching_db_session.commit()

    client = _client_bound_to(matching_db_session, sub="employer-dedupe")
    try:
        response = client.patch(
            f"/jobs/{job.id}/details", headers=API_KEY_HEADERS,
            json={
                "title": "X", "company_name": "Y", "description": "Z",
                "location_city": "Austin",
                "required_skill_ids": [shared_skill.id], "preferred_skill_ids": [shared_skill.id],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["required_skill_ids"] == [shared_skill.id]
        assert body["preferred_skill_ids"] == []
    finally:
        app.dependency_overrides.clear()


def test_create_job_rejects_more_than_ten_required_skills(matching_db_session):
    client = _client_bound_to(matching_db_session)
    try:
        response = client.post(
            "/jobs", headers=API_KEY_HEADERS,
            json={
                "title": "X", "company_name": "Y", "description": "Z",
                "location_city": "Austin",
                "required_skill_ids": list(range(1, 12)),
            },
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
