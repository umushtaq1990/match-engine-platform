"""Tests for the Phase 27 demo frontend, using Streamlit's own official
testing framework (`streamlit.testing.v1.AppTest`) — runs the real script
headlessly and simulates widget interactions, no browser needed. HTTP calls
to api/extraction are mocked here; the real end-to-end calls are verified
manually against the live stack (see docs/frontend.md).

Phase 29 added an OIDC login gate (`_require_login()`, see docs/sso.md) in
front of all of this — every pre-existing test here pre-seeds
`id_token_claims` in session_state to simulate an already-logged-in user, so
they keep testing the app UI itself, not the login gate. The login gate's
own behavior is covered separately below, and the real Authorization Code
exchange against a live Keycloak is verified manually (docs/sso.md
Verification & Results), not mocked here.
"""
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

APP_PATH = "app/frontend/streamlit_app.py"


def _logged_in_app() -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.session_state["id_token_claims"] = {"preferred_username": "candidate"}
    return at


def test_app_renders_without_error():
    at = _logged_in_app().run()
    assert not at.exception
    assert "Project JME" in at.title[0].value


def test_candidate_is_the_default_role():
    at = _logged_in_app().run()
    assert at.sidebar.radio[0].value == "Candidate"
    assert any("Candidate" in h.value for h in at.header)


def test_switching_to_employer_role_shows_job_id_input():
    at = _logged_in_app().run()
    at.sidebar.radio[0].set_value("Employer").run()
    assert not at.exception
    assert any("Employer" in h.value for h in at.header)
    assert any("Job ID" in ni.label for ni in at.number_input)


@patch("requests.get")
def test_get_job_matches_renders_results_table(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200,
        headers={"X-Ranking-Mode": "blended"},
        json=lambda: [{
            "entity_id": 101, "label": "Backend Engineer", "eligible": True,
            "ineligible_reason": None,
            "score": {"skill": 0.8, "location": 1.0, "salary": 0.5, "occupation": 1.0, "total": 0.82},
            "skill_gap": {"confirmed_absent_required": [], "not_recorded_required": [],
                          "confirmed_absent_preferred": [], "not_recorded_preferred": []},
            "explanation": "total score 0.82",
        }],
    )
    at = _logged_in_app().run()
    get_matches_button = next(b for b in at.button if b.label == "Get My Job Matches")
    get_matches_button.click().run(timeout=10)

    assert not at.exception
    mock_get.assert_called_once()
    called_url = mock_get.call_args[0][0]
    assert called_url.endswith("/candidates/1/job-matches")
    assert len(at.dataframe) == 1


@patch("requests.get")
def test_get_job_matches_shows_error_on_failure(mock_get):
    mock_get.return_value = MagicMock(status_code=404, text="No candidate with id=999", headers={})
    at = _logged_in_app().run()
    at.number_input[0].set_value(999).run()
    get_matches_button = next(b for b in at.button if b.label == "Get My Job Matches")
    get_matches_button.click().run(timeout=10)

    assert not at.exception
    assert any("404" in e.value for e in at.error)


@patch("requests.post")
def test_upload_cv_rejects_oversized_file_client_side(mock_post):
    at = _logged_in_app().run()
    # No file uploaded in this headless test harness (AppTest has no file-upload
    # simulation), so the upload button stays disabled -- this test only
    # confirms the app doesn't error out with nothing uploaded.
    upload_button = next(b for b in at.button if b.label == "Upload CV")
    assert upload_button.disabled is True
    mock_post.assert_not_called()


@patch("requests.get")
def test_employer_get_candidate_matches_calls_correct_endpoint(mock_get):
    mock_get.return_value = MagicMock(status_code=200, headers={}, json=lambda: [])
    at = _logged_in_app().run()
    at.sidebar.radio[0].set_value("Employer").run()
    get_button = next(b for b in at.button if b.label == "Get Matching Candidates")
    get_button.click().run(timeout=10)

    assert not at.exception
    mock_get.assert_called_once()
    called_url = mock_get.call_args[0][0]
    assert called_url.endswith("/jobs/1/candidate-matches")


# --- Phase 29: OIDC login gate (_require_login) ---------------------------
# The real Authorization Code exchange against a live Keycloak (correct vs.
# wrong password, real redirect, real token response) is verified manually
# against the running stack — see docs/sso.md Verification & Results. These
# tests cover what AppTest *can* exercise headlessly: the gate blocking
# access and generating a correct auth_url when logged out, and not blocking
# access once a session exists.

def test_unauthenticated_user_sees_login_link_not_the_app():
    at = AppTest.from_file(APP_PATH).run()
    assert not at.exception
    assert not at.sidebar.radio  # _require_login() st.stop()s before the role picker ever renders
    login_markdown = next(m for m in at.markdown if "Log in with SSO" in m.value)
    assert "client_id=jme-frontend" in login_markdown.value
    assert "response_type=code" in login_markdown.value
    assert "redirect_uri=http://localhost:8080/" in login_markdown.value


def test_logged_in_user_sees_the_real_app_not_the_login_link():
    at = _logged_in_app().run()
    assert not at.exception
    assert "Project JME" in at.title[0].value
    assert not any("Log in with SSO" in m.value for m in at.markdown)
    assert any("candidate" in b.label for b in at.sidebar.button)


def test_logout_button_clears_session_and_shows_login_link_again():
    at = _logged_in_app().run()
    logout_button = next(b for b in at.sidebar.button if "Log out" in b.label)
    at = logout_button.click().run()
    assert not at.exception
    assert any("Log in with SSO" in m.value for m in at.markdown)
