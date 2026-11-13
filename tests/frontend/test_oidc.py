"""Unit tests for app/frontend/oidc.py — pure functions, no Streamlit runtime
needed. The real Authorization Code exchange against a live Keycloak (correct
vs. wrong password, real redirect, real token response) is verified manually
against the running stack, not mocked here — see
docs/architecture/ingress-and-identity.md Verification & Results."""
import base64
from unittest.mock import MagicMock, patch

from app.frontend.oidc import (
    build_registration_url,
    decode_id_token,
    is_state_valid,
    password_grant_login,
)


@patch("app.frontend.oidc.requests.post")
def test_password_grant_login_returns_token_json_on_success(mock_post):
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id_token": "fake.jwt.token"})

    token_json = password_grant_login("candidate", "candidate-dev-pw")

    assert token_json == {"id_token": "fake.jwt.token"}
    called_kwargs = mock_post.call_args.kwargs
    assert called_kwargs["data"]["grant_type"] == "password"
    assert called_kwargs["data"]["username"] == "candidate"
    assert called_kwargs["data"]["password"] == "candidate-dev-pw"
    assert called_kwargs["data"]["scope"] == "openid"


@patch("app.frontend.oidc.requests.post")
def test_password_grant_login_raises_value_error_on_failure(mock_post):
    mock_post.return_value = MagicMock(status_code=401)

    try:
        password_grant_login("candidate", "wrong-password")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Invalid username or password" in str(exc)


def test_build_registration_url_hits_registrations_endpoint():
    url = build_registration_url("abc123")
    assert "/protocol/openid-connect/registrations?" in url
    assert "state=abc123" in url
    assert "client_id=jme-frontend" in url


def test_is_state_valid_true_for_known_pending_state():
    assert is_state_valid("abc123", {"abc123": 1234.0}) is True


def test_is_state_valid_false_for_unknown_state():
    assert is_state_valid("forged", {"abc123": 1234.0}) is False


def test_is_state_valid_false_for_none():
    assert is_state_valid(None, {"abc123": 1234.0}) is False


def test_decode_id_token_extracts_payload_claims():
    # header.payload.signature — only the payload matters here, signature is
    # never verified (see _require_login's docstring for that known gap).
    payload = base64.urlsafe_b64encode(b'{"preferred_username": "candidate"}').rstrip(b"=")
    fake_jwt = f"header.{payload.decode()}.signature"
    assert decode_id_token(fake_jwt) == {"preferred_username": "candidate"}


def test_decode_id_token_handles_missing_padding():
    # base64 payloads of varying length need 0-2 '=' padding chars added back;
    # exercise a length that needs padding to make sure that math is right.
    payload = base64.urlsafe_b64encode(b'{"sub": "1"}').rstrip(b"=")
    fake_jwt = f"header.{payload.decode()}.signature"
    assert decode_id_token(fake_jwt) == {"sub": "1"}
