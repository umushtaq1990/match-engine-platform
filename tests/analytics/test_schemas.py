"""Taxonomy-validation tests for EventIn — pure Pydantic, no DB required."""
import pytest
from pydantic import ValidationError

from app.analytics.schemas import EventIn


def test_accepts_known_event_type():
    event = EventIn(actor_type="candidate", actor_id=1, event_type="login")
    assert event.event_type == "login"
    assert event.metadata == {}
    assert event.source == "api"


def test_rejects_unknown_event_type():
    with pytest.raises(ValidationError):
        EventIn(actor_type="candidate", actor_id=1, event_type="not_a_real_type")


def test_rejects_unknown_actor_type():
    with pytest.raises(ValidationError):
        EventIn(actor_type="robot", actor_id=1, event_type="login")


def test_rejects_unknown_entity_type():
    with pytest.raises(ValidationError):
        EventIn(
            actor_type="candidate", actor_id=1, event_type="recommendation_shown",
            entity_type="widget", entity_id=1,
        )


def test_entity_type_optional():
    event = EventIn(actor_type="candidate", actor_id=1, event_type="login")
    assert event.entity_type is None
