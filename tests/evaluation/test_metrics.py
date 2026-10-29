import math

from app.evaluation.metrics import (
    dcg_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_precision_at_k_all_relevant():
    assert precision_at_k([1, 2, 3], {1, 2, 3}, k=3) == 1.0


def test_precision_at_k_none_relevant():
    assert precision_at_k([1, 2, 3], {99}, k=3) == 0.0


def test_precision_at_k_partial():
    assert precision_at_k([1, 2, 3, 4], {1, 3}, k=4) == 0.5


def test_precision_at_k_truncates_to_k():
    # only top 2 considered even though list is longer
    assert precision_at_k([1, 99, 2, 3], {1, 2, 3}, k=2) == 0.5


def test_recall_at_k_finds_all_relevant():
    assert recall_at_k([1, 2, 3], {1, 2}, k=3) == 1.0


def test_recall_at_k_partial():
    assert recall_at_k([1, 99], {1, 2}, k=2) == 0.5


def test_recall_at_k_no_relevant_items_is_zero_not_error():
    assert recall_at_k([1, 2, 3], set(), k=3) == 0.0


def test_dcg_at_k_relevant_item_first_scores_higher_than_last():
    dcg_first = dcg_at_k([1, 2, 3], {1}, k=3)
    dcg_last = dcg_at_k([2, 3, 1], {1}, k=3)
    assert dcg_first > dcg_last
    assert dcg_first == 1.0  # 1/log2(2) = 1.0


def test_ndcg_at_k_perfect_ranking_is_one():
    assert ndcg_at_k([1, 2], {1, 2}, k=2) == 1.0


def test_ndcg_at_k_no_relevant_items_is_zero():
    assert ndcg_at_k([1, 2, 3], set(), k=3) == 0.0


def test_ndcg_at_k_worse_ordering_scores_less_than_one():
    # 2 relevant items but the irrelevant one is ranked first
    score = ndcg_at_k([99, 1, 2], {1, 2}, k=3)
    assert 0 < score < 1.0


def test_reciprocal_rank_first_position():
    assert reciprocal_rank([1, 2, 3], {1}) == 1.0


def test_reciprocal_rank_third_position():
    assert math.isclose(reciprocal_rank([9, 8, 1], {1}), 1 / 3)


def test_reciprocal_rank_not_found_is_zero():
    assert reciprocal_rank([1, 2, 3], {99}) == 0.0
