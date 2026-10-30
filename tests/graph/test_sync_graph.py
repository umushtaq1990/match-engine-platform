"""Pure-logic test for app/graph/sync_graph.py's batching — no Neo4j
involved, a fake session records calls instead.
"""
import app.graph.sync_graph as sync_graph_module
from app.graph.sync_graph import _run_batched


class _FakeNeoSession:
    def __init__(self):
        self.calls: list[list[dict]] = []

    def run(self, query, batch):
        self.calls.append(batch)


def test_run_batched_splits_into_chunks(monkeypatch):
    monkeypatch.setattr(sync_graph_module, "BATCH_SIZE", 2)
    session = _FakeNeoSession()
    rows = [{"id": i} for i in range(5)]

    _run_batched(session, "UNWIND $batch AS row MERGE (n {id: row.id})", rows)

    assert len(session.calls) == 3
    assert session.calls[0] == [{"id": 0}, {"id": 1}]
    assert session.calls[1] == [{"id": 2}, {"id": 3}]
    assert session.calls[2] == [{"id": 4}]


def test_run_batched_empty_rows_makes_no_calls():
    session = _FakeNeoSession()
    _run_batched(session, "irrelevant", [])
    assert session.calls == []
