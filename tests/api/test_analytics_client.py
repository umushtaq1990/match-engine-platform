"""The matching API's instrumentation hook must never raise — an analytics
outage must not break a real matching response. See
app/api/analytics_client.py.
"""
import httpx

from app.api import analytics_client


def test_log_recommendations_shown_is_a_noop_for_empty_results():
    # Would raise if it tried to call out with an empty event list.
    analytics_client.log_recommendations_shown(
        actor_type="candidate", actor_id=1, entity_type="job", entity_ids=[],
    )


def test_log_recommendations_shown_swallows_connection_errors(monkeypatch):
    def _raise(*args, **kwargs):
        raise httpx.ConnectError("analytics service unreachable")

    monkeypatch.setattr(analytics_client.httpx, "post", _raise)

    # Must not propagate — this is the whole point of the best-effort design.
    analytics_client.log_recommendations_shown(
        actor_type="candidate", actor_id=1, entity_type="job", entity_ids=[10, 20, 30],
    )
