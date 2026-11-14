"""Silent access-token refresh (issue #26) -- Keycloak access tokens are
short-lived, so without this a long demo session would start getting 401s
mid-use once the token expires. Every client call in streamlit_app.py goes
through call_with_refresh instead of calling the client function directly:
one centralized retry point instead of repeating try/except at each call
site.
"""
import requests
import streamlit as st

from app.frontend.oidc import refresh_access_token


def call_with_refresh(func, *args, **kwargs):
    """Handles both calling styles used in this app: a client-module
    function that calls `.raise_for_status()` (raises `requests.HTTPError`
    on a 401) and a raw `requests.get`/`.post` call site that just returns
    the `Response` object for the caller to inspect `.status_code` on.
    """
    needs_refresh = False
    result = None
    try:
        result = func(*args, **kwargs)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 401:
            raise
        needs_refresh = True
    else:
        if isinstance(result, requests.Response) and result.status_code == 401:
            needs_refresh = True

    if not needs_refresh:
        return result

    try:
        token_json = refresh_access_token(st.session_state["refresh_token"])
    except ValueError:
        # The refresh_token itself has expired -- no recourse but to send
        # the user back to the login screen.
        st.session_state.clear()
        st.error("Your session has expired. Please log in again.")
        st.rerun()
    st.session_state["access_token"] = token_json["access_token"]
    st.session_state["refresh_token"] = token_json["refresh_token"]
    kwargs["access_token"] = st.session_state["access_token"]
    return func(*args, **kwargs)
