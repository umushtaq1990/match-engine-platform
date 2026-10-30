"""DB-touching orchestration tests for app/matching/rank.py — previously
verified only manually against live seeded data (see docs/matching-engine.md,
docs/production-readiness.md). Uses the real Postgres instance via the
`matching_db_session` fixture (tests/conftest.py), with a small, explicit
fixture dataset (`seed_matching_fixture`) so expected scores/eligibility can
be computed by hand and asserted exactly, not just "it returns something."
"""
import pytest

from app.matching.rank import rank_candidates_for_job, rank_jobs_for_candidate
from tests.conftest import seed_matching_fixture


def test_rank_jobs_for_candidate_scores_eligible_match_correctly(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)

    results = rank_jobs_for_candidate(matching_db_session, ids["matching_candidate_id"])

    assert len(results) == 1
    result = results[0]
    assert result.entity_id == ids["job_id"]
    assert result.eligible is True
    assert result.ineligible_reason is None
    # skill: 0.7*1/1 (required Python matched) + 0.3*0/1 (preferred SQL missing) = 0.7
    assert result.score.skill == 0.7
    assert result.score.location == 1.0  # same city
    assert result.score.salary == 1.0  # 55-65k overlaps 50-70k
    assert result.score.occupation == 1.0  # same occupation
    # total = 0.55*0.7 + 0.15*1 + 0.15*1 + 0.15*1
    assert result.score.total == 0.835
    assert result.skill_gap.not_recorded_preferred == {ids["sql_id"]}
    assert result.skill_gap.confirmed_absent_preferred == set()


def test_rank_jobs_for_candidate_excludes_ineligible_by_default(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)

    results = rank_jobs_for_candidate(matching_db_session, ids["non_matching_candidate_id"])

    assert results == []  # zero required skills matched -> ineligible -> filtered out


def test_rank_jobs_for_candidate_includes_ineligible_when_requested(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)

    results = rank_jobs_for_candidate(
        matching_db_session, ids["non_matching_candidate_id"], include_ineligible=True
    )

    assert len(results) == 1
    assert results[0].eligible is False
    assert results[0].ineligible_reason == "Candidate has none of the job's required skills"


def test_rank_candidates_for_job_ranks_matching_candidate_above_non_matching(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)

    results = rank_candidates_for_job(matching_db_session, ids["job_id"], include_ineligible=True)

    assert [r.entity_id for r in results] == [
        ids["matching_candidate_id"], ids["non_matching_candidate_id"],
    ]
    assert results[0].score.total > results[1].score.total


def test_rank_candidates_for_job_excludes_ineligible_by_default(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)

    results = rank_candidates_for_job(matching_db_session, ids["job_id"])

    assert [r.entity_id for r in results] == [ids["matching_candidate_id"]]


def test_rank_jobs_for_candidate_raises_for_unknown_candidate(matching_db_session):
    seed_matching_fixture(matching_db_session)
    with pytest.raises(ValueError):
        rank_jobs_for_candidate(matching_db_session, candidate_id=999999)
