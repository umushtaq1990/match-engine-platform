"""Pure-logic tests for app/embeddings/retrieval.py — no DB/LLM involved."""
import pytest

from app.embeddings.retrieval import BLENDED_WEIGHTS, EMBEDDINGS_ONLY_WEIGHTS, _cosine_similarity


def test_cosine_similarity_identical_vectors():
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_similarity_orthogonal_vectors():
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_similarity_zero_vector_is_handled():
    assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_blended_weights_sum_to_one():
    assert sum(BLENDED_WEIGHTS.values()) == pytest.approx(1.0)


def test_embeddings_only_weights_sum_to_one():
    assert sum(EMBEDDINGS_ONLY_WEIGHTS.values()) == pytest.approx(1.0)


def test_embeddings_only_weights_zero_out_rule_based_components():
    assert EMBEDDINGS_ONLY_WEIGHTS["location"] == 0.0
    assert EMBEDDINGS_ONLY_WEIGHTS["salary"] == 0.0
    assert EMBEDDINGS_ONLY_WEIGHTS["occupation"] == 0.0


def test_embeddings_only_weights_preserve_blended_dimension_ratios():
    # Skills:Experience:Overall should stay 0.35:0.15:0.15 in both modes.
    blended_ratio = BLENDED_WEIGHTS["skills"] / BLENDED_WEIGHTS["experience"]
    embeddings_only_ratio = EMBEDDINGS_ONLY_WEIGHTS["skills"] / EMBEDDINGS_ONLY_WEIGHTS["experience"]
    assert blended_ratio == pytest.approx(embeddings_only_ratio)
