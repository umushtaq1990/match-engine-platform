"""DB-touching tests for app/ingestion/backfill_education.py -- same
monkeypatched-SessionLocal pattern as tests/analytics/test_stream_consumer.py,
but forwarding to the real `matching_db_session` fixture (not a fake) since
this script's whole job is real SQLAlchemy queries/updates. `main()` closes
its own session when done; `_NonClosingSession` swallows that one call so
the fixture's own transaction (and its rollback-based cleanup) stays intact.
"""
import datetime as dt

from app.ingestion import backfill_education
from app.ingestion.backfill_education import DegreeLevel, main
from app.ingestion.source_models import Candidate, Job, Occupation
from tests.conftest import seed_matching_fixture


class _NonClosingSession:
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass  # the fixture owns this session's lifecycle, not main()


def _run_backfill(session, monkeypatch):
    monkeypatch.setattr(backfill_education, "SessionLocal", lambda: _NonClosingSession(session))
    monkeypatch.setattr("sys.argv", ["backfill_education"])  # argparse must not see pytest's own argv
    main()


def test_backfill_assigns_degree_level_only_for_curated_occupation(matching_db_session, monkeypatch):
    ids = seed_matching_fixture(matching_db_session)
    job = matching_db_session.get(Job, ids["job_id"])
    occupation = matching_db_session.get(Occupation, job.occupation_id)
    occupation.domain = "technology"
    matching_db_session.commit()

    padding_candidate = Candidate(
        full_name="No Occupation Candidate", email="no-occ@example-synthetic.test",
        location_city="Nowhere", location_region="NA", years_experience=1,
        primary_occupation_id=None, created_at=dt.datetime.now(dt.timezone.utc),
    )
    matching_db_session.add(padding_candidate)
    matching_db_session.commit()

    _run_backfill(matching_db_session, monkeypatch)

    job = matching_db_session.get(Job, ids["job_id"])
    matching_candidate = matching_db_session.get(Candidate, ids["matching_candidate_id"])
    no_occupation_candidate = matching_db_session.get(Candidate, padding_candidate.id)

    assert job.min_degree_level is not None
    assert matching_candidate.highest_degree_level is not None
    assert matching_candidate.field_of_study is not None
    # No occupation at all -> left NULL, honest "unknown," not fabricated.
    assert no_occupation_candidate.highest_degree_level is None
    assert no_occupation_candidate.field_of_study is None


def test_backfill_leaves_non_curated_occupation_rows_null(matching_db_session, monkeypatch):
    ids = seed_matching_fixture(matching_db_session)
    # seed_matching_fixture's own Occupation has domain=None (not curated).
    occupation = matching_db_session.get(
        Occupation, matching_db_session.get(Job, ids["job_id"]).occupation_id
    )
    assert occupation.domain is None

    _run_backfill(matching_db_session, monkeypatch)

    job = matching_db_session.get(Job, ids["job_id"])
    candidate = matching_db_session.get(Candidate, ids["matching_candidate_id"])
    assert job.min_degree_level is None
    assert candidate.highest_degree_level is None


def test_backfill_is_idempotent_and_does_not_overwrite_existing_values(matching_db_session, monkeypatch):
    ids = seed_matching_fixture(matching_db_session)
    occupation = matching_db_session.get(
        Occupation, matching_db_session.get(Job, ids["job_id"]).occupation_id
    )
    occupation.domain = "technology"
    candidate = matching_db_session.get(Candidate, ids["matching_candidate_id"])
    candidate.highest_degree_level = DegreeLevel.doctorate
    candidate.field_of_study = "Philosophy"
    matching_db_session.commit()

    _run_backfill(matching_db_session, monkeypatch)

    candidate = matching_db_session.get(Candidate, ids["matching_candidate_id"])
    assert candidate.highest_degree_level == DegreeLevel.doctorate
    assert candidate.field_of_study == "Philosophy"
