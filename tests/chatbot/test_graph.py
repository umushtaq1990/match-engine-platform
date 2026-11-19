"""Regression test for a real bug caught live: qwen2.5:1.5b returned
`{"candidate_id": "1"}` instead of the requested `{"entity_type":
"candidate", "entity_id": 1}` for lookup_structured_data's arguments,
because `_TOOL_CALL_SCHEMA`'s `arguments` field has no per-tool structural
constraint. `_normalize_lookup_args` is the fix -- this test locks that
behavior in rather than relying on re-discovering it live again.

Also covers the skill/course extension: those are looked up by name (an
`entity_name`), not id -- a question naturally names a skill/course by
name/title, unlike candidate/job which it names by id.
"""
from app.chatbot.graph import _normalize_lookup_args


def test_normalizes_candidate_id_alias():
    assert _normalize_lookup_args({"candidate_id": "1"}) == {
        "entity_type": "candidate", "entity_id": 1, "entity_name": None,
    }


def test_normalizes_job_id_alias():
    assert _normalize_lookup_args({"job_id": 42}) == {
        "entity_type": "job", "entity_id": 42, "entity_name": None,
    }


def test_accepts_the_canonical_keys_directly():
    assert _normalize_lookup_args({"entity_type": "candidate", "entity_id": 7}) == {
        "entity_type": "candidate", "entity_id": 7, "entity_name": None,
    }


def test_unrecognized_args_return_empty_sentinel():
    assert _normalize_lookup_args({"something_else": 1}) == {
        "entity_type": "", "entity_id": None, "entity_name": None,
    }


def test_normalizes_skill_name_alias():
    assert _normalize_lookup_args({"skill_name": "Python"}) == {
        "entity_type": "skill", "entity_id": None, "entity_name": "Python",
    }


def test_normalizes_course_title_alias():
    assert _normalize_lookup_args({"course_title": "Intro to ML"}) == {
        "entity_type": "course", "entity_id": None, "entity_name": "Intro to ML",
    }


def test_accepts_canonical_entity_name_directly():
    assert _normalize_lookup_args({"entity_type": "course", "entity_name": "Intro to ML"}) == {
        "entity_type": "course", "entity_id": None, "entity_name": "Intro to ML",
    }


def test_recovers_numeric_entity_name_for_job_as_entity_id():
    # Caught live: a "what skills does job X require" question made the
    # model emit {"entity_type": "job", "entity_name": "2522"} instead of
    # "entity_id" -- the skill/course entity_name examples in the prompt
    # bled into job questions that happen to mention skills.
    assert _normalize_lookup_args({"entity_type": "job", "entity_name": "2522"}) == {
        "entity_type": "job", "entity_id": 2522, "entity_name": None,
    }


def test_recovers_numeric_entity_name_for_candidate_as_entity_id():
    assert _normalize_lookup_args({"entity_type": "candidate", "entity_name": "7"}) == {
        "entity_type": "candidate", "entity_id": 7, "entity_name": None,
    }
