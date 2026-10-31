"""Phase 21 Redis Streams consumer (app/analytics/stream_consumer.py).
Pure-logic tests against a fake Redis client and a fake DB session — no
real Postgres or Redis needed, same discipline as
tests/ingestion/test_generate_synthetic_data.py's pure-function tests.
"""
import json

from app.analytics import stream_consumer


class _FakeSession:
    def __init__(self):
        self.added: list = []
        self.committed = False

    def add_all(self, rows):
        self.added.extend(rows)

    def commit(self):
        self.committed = True

    def close(self):
        pass


class _FakeRedis:
    def __init__(self):
        self.acked: list[str] = []

    def xack(self, stream, group, message_id):
        self.acked.append(message_id)


def test_handle_message_inserts_valid_events_and_acks(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(stream_consumer, "SessionLocal", lambda: fake_session)
    client = _FakeRedis()
    payload = json.dumps([
        {
            "actor_type": "candidate", "actor_id": 1,
            "event_type": "recommendation_shown", "entity_type": "job", "entity_id": 10,
            "metadata": {"rank": 1},
        },
    ])

    stream_consumer._handle_message(client, "1-0", {"payload": payload})

    assert len(fake_session.added) == 1
    assert fake_session.added[0].actor_id == 1
    assert fake_session.committed is True
    assert client.acked == ["1-0"]


def test_handle_message_drops_malformed_events_but_still_commits_and_acks(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(stream_consumer, "SessionLocal", lambda: fake_session)
    client = _FakeRedis()
    payload = json.dumps([
        {"actor_type": "not-a-real-actor-type", "actor_id": 1, "event_type": "recommendation_shown"},
    ])

    stream_consumer._handle_message(client, "1-0", {"payload": payload})

    assert fake_session.added == []
    assert fake_session.committed is True
    assert client.acked == ["1-0"]
