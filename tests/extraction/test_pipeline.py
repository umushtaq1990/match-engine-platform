"""Pure-logic tests for app/extraction/pipeline.py — no DB/LLM involved."""
from app.extraction.pipeline import _cosine_similarity, _document_confidence, _parse_llm_output


def test_parse_llm_output_valid_array():
    raw = '[{"phrase": "Python", "requirement_level": "required"}]'
    result = _parse_llm_output(raw)
    assert result == [{"phrase": "Python", "requirement_level": "required"}]


def test_parse_llm_output_empty_array():
    assert _parse_llm_output("[]") == []


def test_parse_llm_output_rejects_non_list_top_level():
    # The real bug found during Phase 16 verification: a small model given a
    # loose format hint emitted a single object, not an array.
    raw = '{"phrase": "Python", "requirement_level": "required"}'
    assert _parse_llm_output(raw) == []


def test_parse_llm_output_rejects_malformed_json():
    assert _parse_llm_output("not json at all") == []


def test_parse_llm_output_defaults_invalid_level_to_required():
    raw = '[{"phrase": "Python", "requirement_level": "sometimes"}]'
    result = _parse_llm_output(raw)
    assert result == [{"phrase": "Python", "requirement_level": "required"}]


def test_parse_llm_output_skips_items_missing_phrase():
    raw = '[{"requirement_level": "required"}, {"phrase": "SQL", "requirement_level": "preferred"}]'
    result = _parse_llm_output(raw)
    assert result == [{"phrase": "SQL", "requirement_level": "preferred"}]


def test_cosine_similarity_identical_vectors():
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_similarity_orthogonal_vectors():
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_similarity_zero_vector_is_handled():
    assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_document_confidence_no_phrases_at_all_is_lowest_bucket():
    # Non-empty text that yields nothing is itself a bad sign, not neutral.
    assert _document_confidence([], []) == 0.0


def test_document_confidence_all_strong_matches_no_unmatched():
    matches = [{"confidence": 1.0}, {"confidence": 0.9}]
    assert _document_confidence(matches, []) == 0.95


def test_document_confidence_penalized_by_unmatched_rate():
    matches = [{"confidence": 1.0}]
    unmatched = [{"phrase": "x", "best_score": 0.5}]
    # avg_match_confidence (1.0) * (1 - unmatched_rate (0.5)) == 0.5
    assert _document_confidence(matches, unmatched) == 0.5
