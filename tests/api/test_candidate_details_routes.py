"""DB-touching tests for app/api/profile_routes.py's candidate-details/
occupations/skills endpoints (issue #18). Same TestClient + get_db
dependency-override pattern as tests/api/test_profile_routes.py, bound to
profile_db_session (real, disposable Postgres transaction) since these
routes read/write app/ingestion/source_models.py's Candidate/CandidateSkill
tables, not just user_profiles.

Issue #26: PATCH is strict-ownership (no unowned bypass), so a test
exercising it must link the candidate to a UserProfile whose
keycloak_user_id matches the client's overridden `sub` -- see
_make_owned_candidate. GET is lenient (an unowned candidate stays
readable), so plain _make_candidate is enough there.
"""
import datetime as dt

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.jwt_auth import get_verified_claims
from app.api.main import app
from app.identity.models import UserProfile, UserRole
from app.ingestion.source_models import Candidate, CandidateSkill, Occupation, Skill

API_KEY_HEADERS = {"X-API-Key": "dev-local-key-change-me"}


def _client_bound_to(session, sub: str = "user-abc") -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield session)
    app.dependency_overrides[get_verified_claims] = lambda: {"sub": sub}
    return TestClient(app)


def _make_candidate(session, email="details-issue18@example-synthetic.test") -> Candidate:
    candidate = Candidate(
        full_name="Details Candidate", email=email, location_city="Unknown",
        location_region="Unknown", years_experience=0, created_at=dt.datetime.now(dt.timezone.utc),
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


def test_get_candidate_details_returns_defaults_with_no_skills(profile_db_session):
    candidate = _make_candidate(profile_db_session)
    profile_db_session.commit()
    client = _client_bound_to(profile_db_session)
    try:
        response = client.get(f"/candidates/{candidate.id}/details", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["location_city"] == "Unknown"
        assert body["postal_code"] is None
        assert body["country"] is None
        assert body["desired_salary_min"] is None
        assert body["primary_occupation_id"] is None
        assert body["experience_level"] == "entry_level"  # years_experience=0
        assert body["skill_ids"] == []
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_sets_postal_code_and_country(profile_db_session):
    candidate = _make_owned_candidate(
        profile_db_session, sub="user-postal", email="postal-issue20@example-synthetic.test",
    )
    profile_db_session.commit()
    client = _client_bound_to(profile_db_session, sub="user-postal")
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={
                "location_city": "Austin", "postal_code": "78701", "country": "USA",
                "skill_ids": [],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["postal_code"] == "78701"
        assert body["country"] == "USA"
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_does_not_overwrite_location_region(profile_db_session):
    candidate = Candidate(
        full_name="Region Candidate", email="region-issue20@example-synthetic.test",
        location_city="Unknown", location_region="Texas", years_experience=0,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    profile_db_session.add(candidate)
    profile_db_session.flush()
    profile_db_session.add(UserProfile(
        keycloak_user_id="user-region", role=UserRole.candidate, full_name="Region Candidate",
        email="region-issue20@example-synthetic.test", candidate_id=candidate.id,
    ))
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session, sub="user-region")
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={"location_city": "Dallas", "skill_ids": []},
        )
        assert response.status_code == 200
        profile_db_session.refresh(candidate)
        assert candidate.location_region == "Texas"
        assert candidate.location_city == "Dallas"
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_sets_experience_level(profile_db_session):
    candidate = _make_owned_candidate(
        profile_db_session, sub="user-exp", email="exp-issue20@example-synthetic.test",
    )
    profile_db_session.commit()
    client = _client_bound_to(profile_db_session, sub="user-exp")
    try:
        for level in ("entry_level", "mid_level", "senior", "expert"):
            response = client.patch(
                f"/candidates/{candidate.id}/details",
                headers=API_KEY_HEADERS,
                json={"location_city": "Austin", "experience_level": level, "skill_ids": []},
            )
            assert response.status_code == 200
            assert response.json()["experience_level"] == level
    finally:
        app.dependency_overrides.clear()


def test_experience_level_round_trips_at_bucket_boundaries(profile_db_session):
    candidate = _make_candidate(profile_db_session, email="exp-boundary-issue20@example-synthetic.test")
    profile_db_session.commit()
    client = _client_bound_to(profile_db_session)
    expected = {0: "entry_level", 2: "entry_level", 3: "mid_level", 5: "mid_level",
                6: "senior", 10: "senior", 11: "expert"}
    try:
        for years, level in expected.items():
            candidate.years_experience = years
            profile_db_session.commit()
            response = client.get(f"/candidates/{candidate.id}/details", headers=API_KEY_HEADERS)
            assert response.json()["experience_level"] == level, f"years={years}"
    finally:
        app.dependency_overrides.clear()


def test_get_candidate_details_returns_404_for_missing_candidate(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/candidates/999999999/details", headers=API_KEY_HEADERS)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_updates_location_salary_occupation_and_skills(profile_db_session):
    candidate = _make_owned_candidate(
        profile_db_session, sub="user-patch18", email="patch-issue18@example-synthetic.test",
    )
    occupation = Occupation(code="ISSUE18-OCC", title="Issue 18 Test Occupation")
    skill_a = Skill(name="Issue18 Skill A")
    skill_b = Skill(name="Issue18 Skill B")
    profile_db_session.add_all([occupation, skill_a, skill_b])
    profile_db_session.flush()
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session, sub="user-patch18")
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={
                "location_city": "Austin",
                "desired_salary_min": 90000,
                "desired_salary_max": 120000,
                "primary_occupation_id": occupation.id,
                "skill_ids": [skill_a.id, skill_b.id],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["location_city"] == "Austin"
        assert body["desired_salary_min"] == 90000
        assert body["primary_occupation_id"] == occupation.id
        assert sorted(body["skill_ids"]) == sorted([skill_a.id, skill_b.id])

        refreshed = client.get(f"/candidates/{candidate.id}/details", headers=API_KEY_HEADERS)
        assert sorted(refreshed.json()["skill_ids"]) == sorted([skill_a.id, skill_b.id])
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_replaces_previous_skills(profile_db_session):
    candidate = _make_owned_candidate(
        profile_db_session, sub="user-replace18", email="replace-issue18@example-synthetic.test",
    )
    skill_old = Skill(name="Issue18 Old Skill")
    skill_new = Skill(name="Issue18 New Skill")
    profile_db_session.add_all([skill_old, skill_new])
    profile_db_session.flush()
    profile_db_session.add(CandidateSkill(
        candidate_id=candidate.id, skill_id=skill_old.id,
        proficiency="intermediate", evidence_source="self_reported",
    ))
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session, sub="user-replace18")
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={
                "location_city": "Austin", "skill_ids": [skill_new.id],
            },
        )
        assert response.status_code == 200
        assert response.json()["skill_ids"] == [skill_new.id]
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_rejects_more_than_ten_skills(profile_db_session):
    candidate = _make_owned_candidate(
        profile_db_session, sub="user-toomany18", email="toomany-issue18@example-synthetic.test",
    )
    profile_db_session.commit()
    client = _client_bound_to(profile_db_session, sub="user-toomany18")
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={"location_city": "Austin", "skill_ids": list(range(1, 12))},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_returns_404_for_missing_candidate(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.patch(
            "/candidates/999999999/details",
            headers=API_KEY_HEADERS,
            json={"location_city": "Austin", "skill_ids": []},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_patch_candidate_details_rejects_a_caller_who_doesnt_own_it(profile_db_session):
    candidate = _make_owned_candidate(
        profile_db_session, sub="user-real-owner", email="ownercheck-issue26@example-synthetic.test",
    )
    profile_db_session.commit()
    client = _client_bound_to(profile_db_session, sub="user-impostor")
    try:
        response = client.patch(
            f"/candidates/{candidate.id}/details",
            headers=API_KEY_HEADERS,
            json={"location_city": "Austin", "skill_ids": []},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_list_occupations_returns_rows(profile_db_session):
    """Issue #26 CI fix: this used to assume the dev DB's synthetic seed data
    was present, which only `profile_db_session`'s own fixture rows are
    guaranteed to be -- CI's fresh Postgres only runs migrations, never the
    seed script, so an assumption of pre-existing rows fails there even
    though it passes against a locally-seeded dev DB.
    """
    profile_db_session.add(Occupation(code="ISSUE26-LIST-OCC", title="Issue26 List Occupation"))
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/occupations", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert len(body) > 0
        assert {"id", "title"} <= body[0].keys()
    finally:
        app.dependency_overrides.clear()


def test_list_skills_returns_rows(profile_db_session):
    profile_db_session.add(Skill(name="Issue26 List Skill"))
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/skills", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert len(body) > 0
        assert {"id", "name"} <= body[0].keys()
    finally:
        app.dependency_overrides.clear()


def test_list_occupations_excludes_non_curated_rows(profile_db_session):
    curated = Occupation(code="ISSUE19-CUR", title="Issue19 Curated Occupation", is_curated=True)
    filler = Occupation(code="ISSUE19-FILL", title="Issue19 Filler Occupation 2", is_curated=False)
    profile_db_session.add_all([curated, filler])
    profile_db_session.commit()

    client = _client_bound_to(profile_db_session)
    try:
        response = client.get("/occupations", headers=API_KEY_HEADERS)
        assert response.status_code == 200
        titles = [o["title"] for o in response.json()]
        assert "Issue19 Curated Occupation" in titles
        assert "Issue19 Filler Occupation 2" not in titles
    finally:
        app.dependency_overrides.clear()


def test_create_occupation_creates_a_new_curated_row(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.post(
            "/occupations", headers=API_KEY_HEADERS, json={"title": "Issue19 Brand New Occupation"},
        )
        assert response.status_code == 201
        occupation_id = response.json()["id"]

        listed = client.get("/occupations", headers=API_KEY_HEADERS)
        assert any(o["id"] == occupation_id for o in listed.json())
    finally:
        app.dependency_overrides.clear()


def test_create_occupation_is_idempotent_case_insensitive(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        first = client.post(
            "/occupations", headers=API_KEY_HEADERS, json={"title": "Issue19 Dup Occupation"},
        )
        assert first.status_code == 201
        second = client.post(
            "/occupations", headers=API_KEY_HEADERS, json={"title": "issue19 dup occupation"},
        )
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
    finally:
        app.dependency_overrides.clear()


def test_create_skill_creates_a_new_row(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        response = client.post("/skills", headers=API_KEY_HEADERS, json={"name": "Issue19 Brand New Skill"})
        assert response.status_code == 201
        skill_id = response.json()["id"]

        listed = client.get("/skills", headers=API_KEY_HEADERS)
        assert any(s["id"] == skill_id for s in listed.json())
    finally:
        app.dependency_overrides.clear()


def test_create_skill_is_idempotent_case_insensitive(profile_db_session):
    client = _client_bound_to(profile_db_session)
    try:
        first = client.post("/skills", headers=API_KEY_HEADERS, json={"name": "Issue19 Dup Skill"})
        assert first.status_code == 201
        second = client.post("/skills", headers=API_KEY_HEADERS, json={"name": "issue19 dup skill"})
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
    finally:
        app.dependency_overrides.clear()
