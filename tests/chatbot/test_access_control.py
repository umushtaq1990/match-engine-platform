from app.chatbot.access_control import (
    VISIBILITY_CONFIDENTIAL,
    VISIBILITY_PUBLIC,
    allowed_visibilities,
)


def test_admin_sees_both_visibilities():
    allowed = allowed_visibilities("admin")
    assert VISIBILITY_PUBLIC in allowed
    assert VISIBILITY_CONFIDENTIAL in allowed


def test_candidate_sees_only_public():
    assert allowed_visibilities("candidate") == [VISIBILITY_PUBLIC]


def test_employer_sees_only_public():
    assert allowed_visibilities("employer") == [VISIBILITY_PUBLIC]


def test_unrecognized_role_fails_closed_to_public_only():
    assert allowed_visibilities("some-unknown-role") == [VISIBILITY_PUBLIC]
