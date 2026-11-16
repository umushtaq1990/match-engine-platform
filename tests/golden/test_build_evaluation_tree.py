"""Pure-logic tests for app/golden/build_evaluation_tree.py -- no DB."""
from app.golden.build_evaluation_tree import _aggregate, _canonical_occupation_title


def test_canonical_title_strips_padding_suffix():
    assert _canonical_occupation_title("Welder 4") == "Welder"


def test_canonical_title_leaves_real_titles_unchanged():
    assert _canonical_occupation_title("Welder") == "Welder"


def test_canonical_title_does_not_touch_internal_digits():
    # A real title with a trailing number embedded mid-string (not a
    # padding suffix) should be untouched -- only a trailing " <digits>" is
    # the padding pattern.
    assert _canonical_occupation_title("Level 3 Support") == "Level 3 Support"


def test_aggregate_groups_and_averages_by_key():
    rows = [
        {"entity_type": "Candidates", "domain": "trades_logistics", "f1": 1.0},
        {"entity_type": "Candidates", "domain": "trades_logistics", "f1": 0.5},
        {"entity_type": "Job Posts", "domain": "trades_logistics", "f1": 0.8},
    ]
    result = _aggregate(rows, "entity_type", "domain")
    assert result[("Candidates", "trades_logistics")] == (2, 0.75)
    assert result[("Job Posts", "trades_logistics")] == (1, 0.8)
