"""Unit tests for app/frontend/keycloak_admin.py (issue #14). Pure requests
calls, mocked here — the real Admin API calls are verified manually against
the live stack, same convention as oidc.py's password_grant_login."""
from unittest.mock import MagicMock, patch

from app.frontend.keycloak_admin import assign_realm_role, create_user, get_admin_token


@patch("app.frontend.keycloak_admin.requests.post")
def test_create_user_returns_the_new_user_id(mock_post):
    mock_post.return_value = MagicMock(
        status_code=201,
        headers={"Location": "http://keycloak:8080/admin/realms/jme/users/user-abc-123"},
    )

    user_id = create_user("ada@example-synthetic.test", "Ada Lovelace", "s3cret-pw", "fake-admin-token")

    assert user_id == "user-abc-123"
    posted_json = mock_post.call_args.kwargs["json"]
    assert posted_json["username"] == "ada@example-synthetic.test"
    assert posted_json["email"] == "ada@example-synthetic.test"
    assert posted_json["firstName"] == "Ada"
    assert posted_json["lastName"] == "Lovelace"
    assert posted_json["credentials"] == [{"type": "password", "value": "s3cret-pw", "temporary": False}]


@patch("app.frontend.keycloak_admin.requests.post")
def test_create_user_raises_value_error_on_duplicate_email(mock_post):
    mock_post.return_value = MagicMock(status_code=409)

    try:
        create_user("ada@example-synthetic.test", "Ada Lovelace", "s3cret-pw", "fake-admin-token")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "already exists" in str(exc)


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
