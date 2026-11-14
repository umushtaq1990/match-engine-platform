"""Unit tests for app/frontend/auth_helpers.py's call_with_refresh (issue
#26) -- the one place a 401 triggers a silent token refresh + retry,
instead of every call site repeating this logic.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.frontend.auth_helpers import call_with_refresh


def _make_401_response() -> requests.Response:
    resp = requests.Response()
    resp.status_code = 401
    return resp


@patch("app.frontend.auth_helpers.refresh_access_token")
@patch("streamlit.session_state", new_callable=dict)
def test_retries_once_after_refreshing_on_a_raised_401(mock_session_state, mock_refresh):
    mock_session_state["refresh_token"] = "old-refresh-token"
    mock_refresh.return_value = {"access_token": "new-access-token", "refresh_token": "new-refresh-token"}

    error_response = _make_401_response()
    func = MagicMock(side_effect=[requests.HTTPError(response=error_response), "success"])

    result = call_with_refresh(func, "arg1", access_token="old-access-token")

    assert result == "success"
    assert func.call_count == 2
    func.assert_called_with("arg1", access_token="new-access-token")
    assert mock_session_state["access_token"] == "new-access-token"
    assert mock_session_state["refresh_token"] == "new-refresh-token"


@patch("streamlit.session_state", new_callable=dict)
def test_retries_once_after_a_401_response_object_not_an_exception(mock_session_state):
    with patch("app.frontend.auth_helpers.refresh_access_token") as mock_refresh:
        mock_session_state["refresh_token"] = "old-refresh-token"
        mock_refresh.return_value = {"access_token": "new-access-token", "refresh_token": "new-refresh-token"}

        ok_response = requests.Response()
        ok_response.status_code = 200
        func = MagicMock(side_effect=[_make_401_response(), ok_response])

        result = call_with_refresh(func, access_token="old-access-token")

        assert result is ok_response
        assert func.call_count == 2


@patch("streamlit.session_state", new_callable=dict)
def test_does_not_retry_on_a_non_401_error(mock_session_state):
    error_response = requests.Response()
    error_response.status_code = 500
    func = MagicMock(side_effect=requests.HTTPError(response=error_response))

    with pytest.raises(requests.HTTPError):
        call_with_refresh(func, access_token="old-access-token")
    assert func.call_count == 1


@patch("streamlit.session_state", new_callable=dict)
def test_passes_through_a_successful_call_untouched(mock_session_state):
    func = MagicMock(return_value="all good")

    result = call_with_refresh(func, access_token="old-access-token")

    assert result == "all good"
    func.assert_called_once_with(access_token="old-access-token")
