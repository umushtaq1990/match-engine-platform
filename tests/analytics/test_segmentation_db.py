"""DB-touching tests for app/analytics/segmentation.py's feature computation
— real aggregate SQL against a real (transaction-wrapped, rolled-back)
Postgres table, not mocked.
"""
import datetime as dt

from app.analytics.models import BehavioralEvent
from app.analytics.segmentation import SEGMENT_ACTIVE_SEEKER, SEGMENT_DORMANT, segment_actor

NOW = dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc)


def _event(actor_id, event_type, days_ago, actor_type="candidate"):
    return BehavioralEvent(
        occurred_at=NOW - dt.timedelta(days=days_ago),
        received_at=NOW - dt.timedelta(days=days_ago),
        actor_type=actor_type, actor_id=actor_id, event_type=event_type,
        source="test", metadata_={},
    )


def test_segment_actor_with_no_events_is_dormant(analytics_db_session):
    result = segment_actor(analytics_db_session, "candidate", 999, now=NOW)

    assert result["segment"] == SEGMENT_DORMANT
    assert result["days_since_last_event"] is None
    assert result["conversion_rate"] is None


def test_segment_actor_recent_and_frequent_is_active_seeker(analytics_db_session):
    analytics_db_session.add_all([
        _event(1, "recommendation_shown", days_ago=1),
        _event(1, "recommendation_shown", days_ago=2),
        _event(1, "recommendation_clicked", days_ago=2),
        _event(1, "job_applied", days_ago=2),
        _event(1, "login", days_ago=3),
        _event(1, "login", days_ago=10),
    ])
    analytics_db_session.commit()

    result = segment_actor(analytics_db_session, "candidate", 1, now=NOW)

    assert result["segment"] == SEGMENT_ACTIVE_SEEKER
    assert result["days_since_last_event"] == 1
    assert result["event_count_window"] == 6
    assert result["shown_count"] == 2
    assert result["applied_count"] == 1
    assert result["conversion_rate"] == 0.5


def test_segment_actor_old_last_event_is_dormant_regardless_of_past_volume(analytics_db_session):
    analytics_db_session.add_all([
        _event(2, "recommendation_shown", days_ago=45),
        _event(2, "job_applied", days_ago=45),
    ])
    analytics_db_session.commit()

    result = segment_actor(analytics_db_session, "candidate", 2, now=NOW)

    assert result["segment"] == SEGMENT_DORMANT
    assert result["days_since_last_event"] == 45


def test_segment_actor_is_scoped_to_actor_type_and_id(analytics_db_session):
    analytics_db_session.add_all([
        _event(3, "recommendation_shown", days_ago=1, actor_type="candidate"),
        _event(3, "recommendation_shown", days_ago=1, actor_type="employer"),
    ])
    analytics_db_session.commit()

    result = segment_actor(analytics_db_session, "candidate", 3, now=NOW)

    assert result["event_count_window"] == 1
    assert result["shown_count"] == 1
