"""Unit tests for app/frontend/keycloak_admin.py (issue #14). Pure requests
calls, mocked here — the real Admin API calls are verified manually against
the live stack, same convention as oidc.py's exchange_code_for_token."""
from unittest.mock import MagicMock, patch

from app.frontend.keycloak_admin import assign_realm_role, get_admin_token


@patch("app.frontend.keycloak_admin.requests.post")
def test_get_admin_token_returns_access_token(mock_post):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"access_token": "fake-admin-token"},
    )
    mock_post.return_value.raise_for_status = lambda: None

    token = get_admin_token()

    assert token == "fake-admin-token"
    called_kwargs = mock_post.call_args.kwargs
    assert called_kwargs["data"]["grant_type"] == "client_credentials"


@patch("app.frontend.keycloak_admin.requests.post")
@patch("app.frontend.keycloak_admin.requests.get")
def test_assign_realm_role_looks_up_role_then_assigns_it(mock_get, mock_post):
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"id": "role-123", "name": "candidate"})
    mock_get.return_value.raise_for_status = lambda: None
    mock_post.return_value = MagicMock(status_code=204)
    mock_post.return_value.raise_for_status = lambda: None

    assign_realm_role("user-abc", "candidate", "fake-admin-token")

    role_lookup_url = mock_get.call_args.args[0]
    assert role_lookup_url.endswith("/roles/candidate")

    assign_call = mock_post.call_args
    assign_url = assign_call.args[0]
    assert assign_url.endswith("/users/user-abc/role-mappings/realm")
    assert assign_call.kwargs["json"] == [{"id": "role-123", "name": "candidate"}]
