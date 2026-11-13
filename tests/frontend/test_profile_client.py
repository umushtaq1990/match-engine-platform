"""Unit tests for app/frontend/profile_client.py (issue #16). Pure requests
calls, mocked here — the real API calls are verified manually against the
live stack, same convention as keycloak_admin.py's tests.

Issue #26: both functions take a verified access_token now, sent as a
bearer token -- identity comes from that token server-side, not a
client-supplied keycloak_user_id body field (create_profile no longer
takes one at all).
"""
from unittest.mock import MagicMock, patch

from app.frontend.profile_client import create_profile, get_profile


@patch("app.frontend.profile_client.requests.get")
def test_get_profile_returns_none_on_404(mock_get):
    mock_get.return_value = MagicMock(status_code=404)

    assert get_profile("user-abc", access_token="fake-token") is None


@patch("app.frontend.profile_client.requests.get")
def test_get_profile_returns_the_profile_when_found(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"keycloak_user_id": "user-abc", "role": "candidate"},
    )
    mock_get.return_value.raise_for_status = lambda: None

    profile = get_profile("user-abc", access_token="fake-token")

    assert profile == {"keycloak_user_id": "user-abc", "role": "candidate"}
    called_url = mock_get.call_args.args[0]
    assert called_url.endswith("/profiles/user-abc")
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer fake-token"


@patch("app.frontend.profile_client.requests.post")
def test_create_profile_posts_the_expected_payload(mock_post):
    mock_post.return_value = MagicMock(
        status_code=201,
        json=lambda: {"keycloak_user_id": "user-abc", "role": "employer", "company_name": "Acme Co"},
    )
    mock_post.return_value.raise_for_status = lambda: None

    profile = create_profile(
        role="employer", full_name="Grace Hopper",
        email="grace@example-synthetic.test", company_name="Acme Co",
        access_token="fake-token",
    )

    assert profile["role"] == "employer"
    called_url = mock_post.call_args.args[0]
    assert called_url.endswith("/profiles")
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer fake-token"
    posted_json = mock_post.call_args.kwargs["json"]
    assert posted_json == {
        "role": "employer", "full_name": "Grace Hopper",
        "email": "grace@example-synthetic.test", "company_name": "Acme Co",
    }
