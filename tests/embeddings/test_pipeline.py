"""Pure-logic tests for app/embeddings/pipeline.py — no DB/LLM involved."""
from app.embeddings.pipeline import PROFICIENCY_WEIGHTS, REQUIREMENT_WEIGHTS, _weighted_average
from app.ingestion.source_models import ProficiencyLevel, RequirementLevel


def test_weighted_average_single_vector_equals_itself():
    result = _weighted_average([([1.0, 2.0, 3.0], 1.0)])
    assert result == [1.0, 2.0, 3.0]


def test_weighted_average_equal_weights_is_plain_mean():
    result = _weighted_average([([1.0, 0.0], 1.0), ([0.0, 1.0], 1.0)])
    assert result == [0.5, 0.5]


def test_weighted_average_weights_skew_the_result():
    result = _weighted_average([([1.0, 0.0], 3.0), ([0.0, 1.0], 1.0)])
    assert result == [0.75, 0.25]


def test_weighted_average_empty_returns_none():
    assert _weighted_average([]) is None


def test_weighted_average_zero_total_weight_returns_none():
    assert _weighted_average([([1.0, 0.0], 0.0)]) is None


def test_proficiency_weights_cover_every_level():
    assert set(PROFICIENCY_WEIGHTS.keys()) == set(ProficiencyLevel)
    assert all(0.0 < w <= 1.0 for w in PROFICIENCY_WEIGHTS.values())


def test_requirement_weights_cover_every_level():
    assert set(REQUIREMENT_WEIGHTS.keys()) == set(RequirementLevel)
    assert REQUIREMENT_WEIGHTS[RequirementLevel.required] > REQUIREMENT_WEIGHTS[RequirementLevel.preferred]
