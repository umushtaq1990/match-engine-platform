"""Pure-logic tests for app/graph/cluster_entities.py — no Neo4j involved."""
from app.graph.cluster_entities import _group_by_community, compute_mean_occupation_purity


def test_group_by_community_groups_rows():
    rows = [
        {"communityId": 1, "id": 10, "occupation_title": "Data Analyst"},
        {"communityId": 1, "id": 11, "occupation_title": "Data Analyst"},
        {"communityId": 2, "id": 12, "occupation_title": "Electrician"},
    ]
    result = _group_by_community(rows)
    assert result == {
        1: [
            {"id": 10, "occupation_title": "Data Analyst"},
            {"id": 11, "occupation_title": "Data Analyst"},
        ],
        2: [{"id": 12, "occupation_title": "Electrician"}],
    }


def test_purity_perfect_when_each_community_is_a_single_occupation():
    communities = {
        1: [{"id": 1, "occupation_title": "Data Analyst"}, {"id": 2, "occupation_title": "Data Analyst"}],
        2: [{"id": 3, "occupation_title": "Electrician"}],
    }
    assert compute_mean_occupation_purity(communities) == 1.0


def test_purity_half_when_community_is_an_even_split():
    communities = {
        1: [
            {"id": 1, "occupation_title": "Data Analyst"},
            {"id": 2, "occupation_title": "Electrician"},
        ],
    }
    assert compute_mean_occupation_purity(communities) == 0.5


def test_purity_empty_communities_returns_zero():
    assert compute_mean_occupation_purity({}) == 0.0


def test_purity_treats_none_occupation_as_its_own_bucket():
    communities = {
        1: [
            {"id": 1, "occupation_title": None}, {"id": 2, "occupation_title": None},
            {"id": 3, "occupation_title": "Data Analyst"},
        ],  # purity 2/3
    }
    assert compute_mean_occupation_purity(communities) == 2 / 3
