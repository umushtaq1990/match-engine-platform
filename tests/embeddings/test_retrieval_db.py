"""DB-touching tests for Phase 22's blended-default ranking
(app/embeddings/retrieval.py's `rank_jobs_for_candidate_blended_default`).

Explicitly deletes any pre-existing entity_embeddings rows for the fixture's
entity ids before each assertion: `matching_db_session` truncates the core
matching tables (which restarts their ids at 1), but entity_embeddings has
no FK into those tables by design (see app/embeddings/models.py) and isn't
truncated, so a real `embeddings-compute` run against the actual seeded
dataset could otherwise leave rows at the same ids these tests reuse.
"""
import datetime as dt

from sqlalchemy import delete

from app.embeddings.models import EntityEmbedding
from app.embeddings.retrieval import DEFAULT_EMBEDDING_MODEL, rank_jobs_for_candidate_blended_default
from tests.conftest import seed_matching_fixture


def _clear_embeddings(session, entity_ids: dict[str, int]) -> None:
    session.execute(
        delete(EntityEmbedding).where(
            EntityEmbedding.entity_type.in_(list(entity_ids.keys())),
            EntityEmbedding.entity_id.in_(list(entity_ids.values())),
        )
    )
    session.commit()


def test_blended_default_falls_back_to_rule_based_without_embeddings(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)
    _clear_embeddings(matching_db_session, {
        "candidate": ids["matching_candidate_id"], "job": ids["job_id"],
    })

    results, ranking_mode = rank_jobs_for_candidate_blended_default(
        matching_db_session, ids["matching_candidate_id"]
    )

    assert ranking_mode == "rule_based_no_embeddings"
    assert len(results) == 1
    assert results[0].entity_id == ids["job_id"]
    assert results[0].score.total == 0.835  # unchanged from the rule-based-only assertion


def test_blended_default_uses_blended_scoring_when_embeddings_exist(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)
    _clear_embeddings(matching_db_session, {
        "candidate": ids["matching_candidate_id"], "job": ids["job_id"],
    })
    now = dt.datetime.now(dt.timezone.utc)
    vector = [0.1] * 384
    matching_db_session.add(EntityEmbedding(
        entity_type="candidate", entity_id=ids["matching_candidate_id"], dimension="skills",
        embedding_model=DEFAULT_EMBEDDING_MODEL, source_hash="test", vector=vector, computed_at=now,
    ))
    matching_db_session.add(EntityEmbedding(
        entity_type="job", entity_id=ids["job_id"], dimension="skills",
        embedding_model=DEFAULT_EMBEDDING_MODEL, source_hash="test", vector=vector, computed_at=now,
    ))
    matching_db_session.commit()

    results, ranking_mode = rank_jobs_for_candidate_blended_default(
        matching_db_session, ids["matching_candidate_id"]
    )

    assert ranking_mode == "blended"
    assert len(results) == 1
    assert results[0].entity_id == ids["job_id"]
    # Eligibility/skill_gap still come from the rule-based pass — embeddings
    # only re-order, they don't replace these fields.
    assert results[0].eligible is True
    assert results[0].skill_gap.not_recorded_preferred == {ids["sql_id"]}


def test_blended_default_returns_rule_based_for_candidate_with_no_eligible_jobs(matching_db_session):
    ids = seed_matching_fixture(matching_db_session)

    results, ranking_mode = rank_jobs_for_candidate_blended_default(
        matching_db_session, ids["non_matching_candidate_id"]
    )

    assert results == []
    assert ranking_mode == "rule_based"
