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
import base64
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

APP_PATH = "app/frontend/streamlit_app.py"


def _fake_id_token(preferred_username: str, sub: str = "user-1") -> str:
    payload = base64.urlsafe_b64encode(
        f'{{"preferred_username": "{preferred_username}", "sub": "{sub}", "realm_access": {{"roles": []}}}}'.encode()
    ).rstrip(b"=")
    return f"header.{payload.decode()}.signature"


def _logged_in_app(role: str = "candidate") -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.session_state["id_token_claims"] = {
        "preferred_username": role,
        "sub": f"user-{role}",
        "realm_access": {"roles": [role]},
    }
    # Pre-seed a completed profile (issue #15/#16) so these tests exercise the
    # app UI itself, not the one-time profile-capture form — same bypass
    # pattern as pre-seeding id_token_claims above to skip the login gate.
    at.session_state["profile"] = {"role": role, "full_name": f"Demo {role.title()}"}
    return at


def test_app_renders_without_error():
    at = _logged_in_app().run()
    assert not at.exception
    assert "Talent Matching Platform" in at.title[0].value


def test_candidate_role_claim_shows_candidate_flow():
    at = _logged_in_app(role="candidate").run()
    assert not at.exception
    assert any("Candidate" in h.value for h in at.header)


def test_employer_role_claim_shows_job_id_input():
    at = _logged_in_app(role="employer").run()
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
    at = _logged_in_app(role="employer").run()
    get_button = next(b for b in at.button if b.label == "Get Matching Candidates")
    get_button.click().run(timeout=10)

    assert not at.exception
    mock_get.assert_called_once()
    called_url = mock_get.call_args[0][0]
    assert called_url.endswith("/jobs/1/candidate-matches")


# --- OIDC login gate (_require_login) --------------------------------------
# Login uses a direct Resource Owner Password grant rendered inline on this
# page (text_input fields, see _require_login's docstring); registration
# still redirects to Keycloak's own hosted form and back via the
# Authorization Code flow. The real Keycloak calls (correct vs. wrong
# password, real registration redirect, real token response) are verified
# manually against the running stack — see
# docs/architecture/ingress-and-identity.md Verification & Results. These
# tests cover what AppTest *can* exercise headlessly: the gate blocking
# access, the inline login form's username/password submission, and not
# blocking access once a session exists.

def test_unauthenticated_user_sees_login_form_not_the_app():
    at = AppTest.from_file(APP_PATH).run()
    assert not at.exception
    assert not at.number_input  # _require_login() st.stop()s before the rest of the app ever renders
    assert any("Log in" in m.value for m in at.markdown)
    assert len(at.text_input) == 2
    assert any(b.label == "Log in" for b in at.button)
    registration_markdown = next(m for m in at.markdown if "Register" in m.value)
    assert "/protocol/openid-connect/registrations?" in registration_markdown.value


@patch("app.frontend.profile_client.get_profile")
@patch("app.frontend.oidc.requests.post")
def test_submitting_valid_credentials_logs_in(mock_token_post, mock_get_profile):
    mock_token_post.return_value = MagicMock(
        status_code=200, json=lambda: {"id_token": _fake_id_token("candidate", sub="user-candidate")},
    )
    mock_get_profile.return_value = None
    at = AppTest.from_file(APP_PATH).run()
    at.text_input[0].set_value("candidate").run()
    at.text_input[1].set_value("candidate-dev-pw").run()
    login_button = next(b for b in at.button if b.label == "Log in")
    at = login_button.click().run()

    assert not at.exception
    assert at.session_state["id_token_claims"]["preferred_username"] == "candidate"
    # Past the login gate now, straight into the first-login profile form.
    assert any("Tell us a bit about yourself" in s.value for s in at.subheader)


@patch("app.frontend.oidc.requests.post")
def test_submitting_invalid_credentials_shows_error_and_stays_on_login(mock_token_post):
    mock_token_post.return_value = MagicMock(status_code=401)
    at = AppTest.from_file(APP_PATH).run()
    at.text_input[0].set_value("candidate").run()
    at.text_input[1].set_value("wrong-password").run()
    login_button = next(b for b in at.button if b.label == "Log in")
    at = login_button.click().run()

    assert not at.exception
    assert "id_token_claims" not in at.session_state
    assert any("Invalid username or password" in e.value for e in at.error)


# --- Issue #15/#16: first-login profile-capture form for users with no
# user_profiles row yet ------------------------------------------------------

def _logged_in_app_without_profile() -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.session_state["id_token_claims"] = {
        "preferred_username": "newuser",
        "sub": "user-newuser",
        "email": "newuser@example-synthetic.test",
        "realm_access": {"roles": []},
    }
    return at


@patch("app.frontend.profile_client.get_profile")
def test_new_user_with_no_profile_sees_profile_form_not_the_app(mock_get_profile):
    mock_get_profile.return_value = None
    at = _logged_in_app_without_profile().run()
    assert not at.exception
    assert not at.number_input  # neither Candidate nor Employer flow rendered yet
    assert any("Tell us a bit about yourself" in s.value for s in at.subheader)
    assert at.text_input
    assert at.radio
    assert any(b.label == "Continue" for b in at.button)


@patch("app.frontend.profile_client.create_profile")
@patch("app.frontend.profile_client.get_profile")
@patch("app.frontend.keycloak_admin.assign_realm_role")
@patch("app.frontend.keycloak_admin.get_admin_token")
def test_submitting_profile_form_as_candidate_assigns_role_and_shows_candidate_flow(
    mock_get_token, mock_assign_role, mock_get_profile, mock_create_profile,
):
    mock_get_token.return_value = "fake-admin-token"
    mock_get_profile.return_value = None
    mock_create_profile.return_value = {"role": "candidate", "full_name": "Ada Lovelace"}
    at = _logged_in_app_without_profile().run()
    at.text_input[0].set_value("Ada Lovelace").run()
    at.radio[0].set_value("Candidate").run()
    continue_button = next(b for b in at.button if b.label == "Continue")
    at = continue_button.click().run()

    assert not at.exception
    mock_assign_role.assert_called_once_with("user-newuser", "candidate", "fake-admin-token")
    mock_create_profile.assert_called_once_with(
        keycloak_user_id="user-newuser", role="candidate", full_name="Ada Lovelace",
        email="newuser@example-synthetic.test", company_name=None,
    )
    assert any("Candidate" in h.value for h in at.header)


@patch("app.frontend.profile_client.create_profile")
@patch("app.frontend.profile_client.get_profile")
@patch("app.frontend.keycloak_admin.assign_realm_role")
@patch("app.frontend.keycloak_admin.get_admin_token")
def test_submitting_profile_form_as_employer_captures_company_name(
    mock_get_token, mock_assign_role, mock_get_profile, mock_create_profile,
):
    mock_get_token.return_value = "fake-admin-token"
    mock_get_profile.return_value = None
    mock_create_profile.return_value = {"role": "employer", "full_name": "Grace Hopper"}
    at = _logged_in_app_without_profile().run()
    at.text_input[0].set_value("Grace Hopper").run()
    at.radio[0].set_value("Employer").run()
    at.text_input[1].set_value("Acme Co").run()
    continue_button = next(b for b in at.button if b.label == "Continue")
    at = continue_button.click().run()

    assert not at.exception
    mock_create_profile.assert_called_once_with(
        keycloak_user_id="user-newuser", role="employer", full_name="Grace Hopper",
        email="newuser@example-synthetic.test", company_name="Acme Co",
    )
    assert any("Employer" in h.value for h in at.header)


def test_logged_in_user_sees_the_real_app_not_the_login_link():
    at = _logged_in_app().run()
    assert not at.exception
    assert "Talent Matching Platform" in at.title[0].value
    assert not any("Log in" in m.value for m in at.markdown)
    assert any("candidate" in b.label for b in at.sidebar.button)


def test_logout_button_clears_session_and_shows_login_link_again():
    at = _logged_in_app().run()
    logout_button = next(b for b in at.sidebar.button if "Log out" in b.label)
    at = logout_button.click().run()
    assert not at.exception
    assert any("Log in" in m.value for m in at.markdown)
