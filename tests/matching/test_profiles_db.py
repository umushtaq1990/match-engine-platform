"""DB-touching test for app/matching/profiles.py's load_job_profiles_by_ids
— added in Phase 20 after a real stress test found app/embeddings/
retrieval.py calling load_job_profile in a per-job loop (the exact N+1
Phase 9 already fixed once). See docs/data-at-scale.md.
"""
from app.matching.profiles import load_job_profiles_by_ids
from tests.conftest import seed_matching_fixture


def test_load_job_profiles_by_ids_returns_requested_jobs(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)

    profiles = load_job_profiles_by_ids(matching_db_session, [ids["job_id"]])

    assert set(profiles.keys()) == {ids["job_id"]}
    profile = profiles[ids["job_id"]]
    assert profile.required_skill_ids == {ids["python_id"]}
    assert profile.preferred_skill_ids == {ids["sql_id"]}


def test_load_job_profiles_by_ids_empty_list_returns_empty_dict(matching_db_session):
    seed_matching_fixture(matching_db_session)
    assert load_job_profiles_by_ids(matching_db_session, []) == {}


def test_load_job_profiles_by_ids_ignores_unknown_ids(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)

    profiles = load_job_profiles_by_ids(matching_db_session, [ids["job_id"], 999999])

    assert set(profiles.keys()) == {ids["job_id"]}
