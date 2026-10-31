"""Pure-logic tests for app/analytics/segmentation.py — no DB involved."""
from app.analytics.segmentation import (
    SEGMENT_ACTIVE_SEEKER,
    SEGMENT_DORMANT,
    SEGMENT_PASSIVE_SEEKER,
    compute_segment_label,
)


def test_no_events_ever_is_dormant():
    assert compute_segment_label(None, 0, None) == SEGMENT_DORMANT


def test_last_event_beyond_threshold_is_dormant():
    assert compute_segment_label(31, 20, 0.9) == SEGMENT_DORMANT


def test_recent_and_frequent_is_active_seeker():
    assert compute_segment_label(1, 5, None) == SEGMENT_ACTIVE_SEEKER


def test_recent_but_infrequent_with_high_conversion_is_active_seeker():
    assert compute_segment_label(5, 1, 0.5) == SEGMENT_ACTIVE_SEEKER


def test_recent_but_infrequent_and_low_conversion_is_passive_seeker():
    assert compute_segment_label(5, 1, 0.1) == SEGMENT_PASSIVE_SEEKER


def test_recent_but_infrequent_with_no_conversion_signal_is_passive_seeker():
    assert compute_segment_label(5, 2, None) == SEGMENT_PASSIVE_SEEKER


def test_boundary_at_dormant_threshold_is_not_dormant():
    # Exactly 30 days — still counts as recent enough, 31 is the cutover.
    assert compute_segment_label(30, 0, None) == SEGMENT_PASSIVE_SEEKER
