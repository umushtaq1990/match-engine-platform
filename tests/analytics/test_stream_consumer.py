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
    def __init__(self, pending=None, stream_contents=None):
        self.acked: list[str] = []
        self.claimed: list[str] = []
        self.dead_lettered: list[dict] = []
        self._pending = pending or []
        self._stream_contents = stream_contents or {}

    def xack(self, stream, group, message_id):
        self.acked.append(message_id)

    def xpending_range(self, stream, group, min, max, count, idle=None):
        return self._pending

    def xclaim(self, stream, group, consumer, min_idle_time, message_ids):
        results = []
        for message_id in message_ids:
            if message_id in self._stream_contents:
                self.claimed.append(message_id)
                results.append((message_id, self._stream_contents[message_id]))
        return results

    def xrange(self, stream, min, max):
        if min in self._stream_contents:
            return [(min, self._stream_contents[min])]
        return []

    def xadd(self, stream, fields, maxlen=None, approximate=None):
        self.dead_lettered.append({"stream": stream, "fields": fields})


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


def test_reclaim_stale_messages_retries_a_message_under_the_delivery_limit(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(stream_consumer, "SessionLocal", lambda: fake_session)
    payload = json.dumps([
        {"actor_type": "candidate", "actor_id": 1, "event_type": "recommendation_shown"},
    ])
    client = _FakeRedis(
        pending=[{"message_id": "1-0", "consumer": "dead-worker", "times_delivered": 2}],
        stream_contents={"1-0": {"payload": payload}},
    )

    stream_consumer._reclaim_stale_messages(client)

    assert client.claimed == ["1-0"]
    assert client.acked == ["1-0"]
    assert fake_session.committed is True
    assert client.dead_lettered == []


def test_reclaim_stale_messages_dead_letters_a_message_at_the_delivery_limit(monkeypatch):
    client = _FakeRedis(
        pending=[{
            "message_id": "2-0", "consumer": "dead-worker",
            "times_delivered": stream_consumer.MAX_DELIVERIES,
        }],
        stream_contents={"2-0": {"payload": "whatever"}},
    )

    stream_consumer._reclaim_stale_messages(client)

    assert client.claimed == []  # never re-attempted — straight to dead-letter
    assert client.acked == ["2-0"]
    assert len(client.dead_lettered) == 1
    dead = client.dead_lettered[0]
    assert dead["stream"] == stream_consumer.DEAD_LETTER_STREAM
    assert dead["fields"]["original_id"] == "2-0"
    assert dead["fields"]["times_delivered"] == str(stream_consumer.MAX_DELIVERIES)


def test_reclaim_stale_messages_acks_even_if_the_trimmed_entry_is_gone(monkeypatch):
    client = _FakeRedis(
        pending=[{
            "message_id": "3-0", "consumer": "dead-worker",
            "times_delivered": stream_consumer.MAX_DELIVERIES,
        }],
        stream_contents={},  # already trimmed from the stream by MAXLEN
    )

    stream_consumer._reclaim_stale_messages(client)

    assert client.acked == ["3-0"]
    assert client.dead_lettered == []
