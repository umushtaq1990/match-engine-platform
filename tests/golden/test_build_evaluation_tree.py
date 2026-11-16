"""Pure-logic tests for app/golden/build_evaluation_tree.py -- no DB."""
from app.golden.build_evaluation_tree import _aggregate, _icicle_trace


def test_aggregate_groups_and_averages_by_value_key():
    rows = [
        {"entity_type": "Candidates", "domain": "skilled_trades", "f1": 1.0},
        {"entity_type": "Candidates", "domain": "skilled_trades", "f1": 0.5},
        {"entity_type": "Job Posts", "domain": "skilled_trades", "f1": 0.8},
    ]
    result = _aggregate(rows, "f1", "entity_type", "domain")
    assert result[("Candidates", "skilled_trades")] == (2, 0.75)
    assert result[("Job Posts", "skilled_trades")] == (1, 0.8)


def test_icicle_trace_root_value_matches_total_row_count():
    rows = [
        {"entity_type": "Candidates", "domain": "technology", "f1": 1.0},
        {"entity_type": "Candidates", "domain": "healthcare", "f1": 0.5},
        {"entity_type": "Job Posts", "domain": "technology", "f1": 0.8},
    ]
    trace = _icicle_trace(rows, "f1", "All examples", [("entity_type",), ("entity_type", "domain")])

    assert trace.ids[0] == "root"
    assert trace.values[0] == len(rows)
    # branchvalues="total" requires each level's children to sum to their
    # parent -- the two entity_type nodes (depth 0) should sum back to root.
    entity_type_values = [v for i, v in zip(trace.ids, trace.values) if i.count("|") == 1]
    assert sum(entity_type_values) == len(rows)


def test_icicle_trace_parents_correctly_nested():
    rows = [
        {"entity_type": "Candidates", "domain": "technology", "occupation": "Cloud Engineer", "f1": 1.0},
        {"entity_type": "Candidates", "domain": "technology", "occupation": "DevOps Engineer", "f1": 0.6},
    ]
    trace = _icicle_trace(
        rows, "f1", "All examples",
        [("entity_type",), ("entity_type", "domain"), ("entity_type", "domain", "occupation")],
    )

    by_id = dict(zip(trace.ids, trace.parents))
    domain_node = "root|entity_type:Candidates|domain:technology"
    occupation_node = "root|entity_type:Candidates|domain:technology|occupation:Cloud Engineer"
    assert by_id[domain_node] == "root|entity_type:Candidates"
    assert by_id[occupation_node] == domain_node
