"""Regression test for a real bug caught live: qwen2.5:1.5b returned
`{"candidate_id": "1"}` instead of the requested `{"entity_type":
"candidate", "entity_id": 1}` for lookup_structured_data's arguments,
because `_TOOL_CALL_SCHEMA`'s `arguments` field has no per-tool structural
constraint. `_normalize_lookup_args` is the fix -- this test locks that
behavior in rather than relying on re-discovering it live again.
"""
from app.chatbot.graph import _normalize_lookup_args


def test_normalizes_candidate_id_alias():
    assert _normalize_lookup_args({"candidate_id": "1"}) == ("candidate", 1)


def test_normalizes_job_id_alias():
    assert _normalize_lookup_args({"job_id": 42}) == ("job", 42)


def test_accepts_the_canonical_keys_directly():
    assert _normalize_lookup_args({"entity_type": "candidate", "entity_id": 7}) == ("candidate", 7)


def test_unrecognized_args_return_empty_sentinel():
    assert _normalize_lookup_args({"something_else": 1}) == ("", -1)
