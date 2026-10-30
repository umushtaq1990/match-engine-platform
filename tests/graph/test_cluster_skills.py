"""Pure-logic tests for app/graph/cluster_skills.py — no Neo4j involved."""
from app.graph.cluster_skills import _group_by_community, compute_mean_category_purity


def test_group_by_community_groups_rows():
    rows = [
        {"communityId": 1, "name": "Python", "category": "programming"},
        {"communityId": 1, "name": "Java", "category": "programming"},
        {"communityId": 2, "name": "Welding", "category": "trades"},
    ]
    result = _group_by_community(rows)
    assert result == {
        1: [
            {"name": "Python", "category": "programming"},
            {"name": "Java", "category": "programming"},
        ],
        2: [{"name": "Welding", "category": "trades"}],
    }


def test_purity_perfect_when_each_community_is_a_single_category():
    communities = {
        1: [{"name": "Python", "category": "programming"}, {"name": "Java", "category": "programming"}],
        2: [{"name": "Welding", "category": "trades"}],
    }
    assert compute_mean_category_purity(communities) == 1.0


def test_purity_half_when_community_is_an_even_split():
    communities = {
        1: [
            {"name": "Python", "category": "programming"},
            {"name": "Welding", "category": "trades"},
        ],
    }
    assert compute_mean_category_purity(communities) == 0.5


def test_purity_empty_communities_returns_zero():
    assert compute_mean_category_purity({}) == 0.0


def test_purity_averages_across_multiple_communities():
    communities = {
        1: [
            {"name": "A", "category": "x"}, {"name": "B", "category": "x"},
            {"name": "C", "category": "y"}, {"name": "D", "category": "x"},
        ],  # purity 3/4 = 0.75
        2: [{"name": "E", "category": "z"}],  # purity 1.0
    }
    assert compute_mean_category_purity(communities) == (0.75 + 1.0) / 2
