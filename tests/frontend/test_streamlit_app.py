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
    # candidate_id (issue #17) is only ever set for role=candidate.
    at.session_state["profile"] = {
        "role": role, "full_name": f"Demo {role.title()}",
        "candidate_id": 42 if role == "candidate" else None,
    }
    return at


def _default_candidate_details() -> dict:
    return {
        "location_city": "Unknown", "postal_code": None, "country": None,
        "desired_salary_min": None, "desired_salary_max": None,
        "primary_occupation_id": None, "experience_level": "entry_level", "skill_ids": [],
    }


def _default_occupations() -> list[dict]:
    return [{"id": 1, "title": "Software Engineer"}]


def _default_skills() -> list[dict]:
    return [{"id": 1, "name": "Python"}, {"id": 2, "name": "SQL"}]


def _candidate_support_endpoint_response(url: str):
    """Issue #18: "Your profile" renders unconditionally alongside "Upload
    your CV", so every candidate-flow test's mocked `requests.get` must also
    answer `/details`, `/occupations`, `/skills` — not just `/cv`. Returns
    `None` for any other URL so callers can fall through to their own logic.
    """
    if url.endswith("/details"):
        return MagicMock(status_code=200, json=_default_candidate_details)
    if url.endswith("/occupations"):
        return MagicMock(status_code=200, json=_default_occupations)
    if url.endswith("/skills"):
        return MagicMock(status_code=200, json=_default_skills)
    return None


def _no_existing_cv(url, *args, **kwargs):
    return _candidate_support_endpoint_response(url) or MagicMock(status_code=404)


@patch("requests.get")
def test_app_renders_without_error(mock_get):
    mock_get.side_effect = _no_existing_cv
    at = _logged_in_app().run()
    assert not at.exception
    assert "Talent Matching Platform" in at.title[0].value


@patch("requests.get")
def test_candidate_role_claim_shows_candidate_flow(mock_get):
    mock_get.side_effect = _no_existing_cv
    at = _logged_in_app(role="candidate").run()
    assert not at.exception
    assert any("Upload your CV" in s.value for s in at.subheader)
    assert any(b.label == "Show Job Matches" for b in at.button)


def test_employer_role_claim_shows_job_id_input():
    at = _logged_in_app(role="employer").run()
    assert not at.exception
    assert any("Employer" in h.value for h in at.header)
    assert any("Job ID" in ni.label for ni in at.number_input)


@patch("requests.get")
def test_existing_cv_is_shown_with_a_replace_option(mock_get):
    def side_effect(url, *args, **kwargs):
        if url.endswith("/cv"):
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "candidate_id": 42, "original_filename": "resume.pdf", "content_type": "application/pdf",
                    "size_bytes": 12345, "uploaded_at": "2026-01-01T00:00:00Z",
                },
            )
        return _candidate_support_endpoint_response(url) or MagicMock(status_code=404)
    mock_get.side_effect = side_effect

    at = _logged_in_app().run()

    assert not at.exception
    assert any("resume.pdf" in i.value for i in at.info)
    assert any("12345 bytes" in i.value for i in at.info)


@patch("requests.get")
def test_no_existing_cv_shows_no_cv_on_file_message(mock_get):
    mock_get.side_effect = _no_existing_cv
    at = _logged_in_app().run()

    assert not at.exception
    assert not at.info


@patch("requests.get")
def test_get_job_matches_renders_results_table(mock_get):
    def side_effect(url, *args, **kwargs):
        if url.endswith("/cv"):
            return MagicMock(status_code=404)
        support = _candidate_support_endpoint_response(url)
        if support is not None:
            return support
        return MagicMock(
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
    mock_get.side_effect = side_effect

    at = _logged_in_app().run()
    get_matches_button = next(b for b in at.button if b.label == "Show Job Matches")
    get_matches_button.click().run(timeout=10)

    assert not at.exception
    called_url, called_kwargs = mock_get.call_args.args[0], mock_get.call_args.kwargs
    assert called_url.endswith("/candidates/42/job-matches")
    assert called_kwargs["params"] == {"top_k": 10}
    assert len(at.dataframe) == 1


@patch("requests.get")
def test_get_job_matches_shows_error_on_failure(mock_get):
    def side_effect(url, *args, **kwargs):
        if url.endswith("/cv"):
            return MagicMock(status_code=404)
        support = _candidate_support_endpoint_response(url)
        if support is not None:
            return support
        return MagicMock(status_code=404, text="No candidate with id=42", headers={})
    mock_get.side_effect = side_effect

    at = _logged_in_app().run()
    get_matches_button = next(b for b in at.button if b.label == "Show Job Matches")
    get_matches_button.click().run(timeout=10)

    assert not at.exception
    assert any("404" in e.value for e in at.error)


@patch("requests.post")
@patch("requests.get")
def test_upload_cv_rejects_oversized_file_client_side(mock_get, mock_post):
    mock_get.side_effect = _no_existing_cv
    at = _logged_in_app().run()
    # No file uploaded in this headless test harness (AppTest has no file-upload
    # simulation), so the upload button stays disabled -- this test only
    # confirms the app doesn't error out with nothing uploaded.
    upload_button = next(b for b in at.button if b.label == "Upload CV")
    assert upload_button.disabled is True
    mock_post.assert_not_called()


@patch("requests.get")
def test_profile_details_form_renders_with_prefilled_values(mock_get):
    def side_effect(url, *args, **kwargs):
        if url.endswith("/cv"):
            return MagicMock(status_code=404)
        if url.endswith("/details"):
            return MagicMock(status_code=200, json=lambda: {
                "location_city": "Austin", "postal_code": "78701", "country": "USA",
                "desired_salary_min": 90000, "desired_salary_max": 120000,
                "primary_occupation_id": 1, "experience_level": "senior", "skill_ids": [2],
            })
        return _candidate_support_endpoint_response(url) or MagicMock(status_code=404)
    mock_get.side_effect = side_effect

    at = _logged_in_app().run()

    assert not at.exception
    assert any("Your profile" in s.value for s in at.subheader)
    assert next(ti for ti in at.text_input if ti.label == "City").value == "Austin"
    assert next(ti for ti in at.text_input if ti.label == "Postal code").value == "78701"
    assert next(ti for ti in at.text_input if ti.label == "Country").value == "USA"
    assert next(ni for ni in at.number_input if ni.label == "Desired salary (min) ($)").value == 90000
    assert next(sb for sb in at.selectbox if sb.label == "Occupation").value == "Software Engineer"
    assert next(sb for sb in at.selectbox if sb.label == "Experience level").value == "Senior"
    assert at.multiselect[0].value == ["SQL"]


@patch("requests.get")
def test_selecting_add_new_occupation_reveals_title_input(mock_get):
    mock_get.side_effect = _no_existing_cv
    at = _logged_in_app().run()
    next(sb for sb in at.selectbox if sb.label == "Occupation").set_value("+ Add a new occupation").run()
    assert not at.exception
    assert any(ti.label == "New occupation title" for ti in at.text_input)


@patch("app.frontend.candidate_client.requests.post")
@patch("app.frontend.candidate_client.requests.patch")
@patch("requests.get")
def test_saving_with_a_new_occupation_and_skill_creates_both_then_patches(mock_get, mock_patch, mock_post):
    mock_get.side_effect = _no_existing_cv
    mock_patch.return_value = MagicMock(status_code=200, json=lambda: _default_candidate_details())

    def post_side_effect(url, *args, **kwargs):
        if url.endswith("/occupations"):
            return MagicMock(status_code=201, json=lambda: {"id": 77, "title": "Robot Wrangler"})
        if url.endswith("/skills"):
            return MagicMock(status_code=201, json=lambda: {"id": 88, "name": "Telekinesis"})
        raise AssertionError(f"unexpected POST to {url}")
    mock_post.side_effect = post_side_effect

    at = _logged_in_app().run()
    next(sb for sb in at.selectbox if sb.label == "Occupation").set_value("+ Add a new occupation").run()
    next(ti for ti in at.text_input if ti.label == "New occupation title").set_value("Robot Wrangler").run()
    next(ti for ti in at.text_input if ti.label == "Add a new skill (optional)").set_value("Telekinesis").run()
    save_button = next(b for b in at.button if b.label == "Save profile details")
    at = save_button.click().run()

    assert not at.exception
    occupation_call = next(c for c in mock_post.call_args_list if c.args[0].endswith("/occupations"))
    assert occupation_call.kwargs["json"] == {"title": "Robot Wrangler"}
    skill_call = next(c for c in mock_post.call_args_list if c.args[0].endswith("/skills"))
    assert skill_call.kwargs["json"] == {"name": "Telekinesis"}
    patch_payload = mock_patch.call_args.kwargs["json"]
    assert patch_payload["primary_occupation_id"] == 77
    assert 88 in patch_payload["skill_ids"]


@patch("app.frontend.candidate_client.requests.patch")
@patch("requests.get")
def test_saving_profile_details_calls_patch_with_expected_payload(mock_get, mock_patch):
    mock_get.side_effect = _no_existing_cv
    mock_patch.return_value = MagicMock(status_code=200, json=lambda: _default_candidate_details())

    at = _logged_in_app().run()
    next(ti for ti in at.text_input if ti.label == "City").set_value("Denver").run()
    at.multiselect[0].set_value(["Python", "SQL"]).run()
    save_button = next(b for b in at.button if b.label == "Save profile details")
    at = save_button.click().run()

    assert not at.exception
    called_url, called_kwargs = mock_patch.call_args.args[0], mock_patch.call_args.kwargs
    assert called_url.endswith("/candidates/42/details")
    assert called_kwargs["json"]["location_city"] == "Denver"
    assert sorted(called_kwargs["json"]["skill_ids"]) == [1, 2]


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


# --- Login/registration gate (_require_login) ------------------------------
# Login and registration are two separate screens on this page (toggled via
# session_state["auth_view"], see _require_login's docstring) — no redirect
# to a separate Keycloak-hosted page for either one. The real Keycloak calls
# (correct vs. wrong password, real user creation, real token response) are
# verified manually against the running stack — see
# docs/architecture/ingress-and-identity.md Verification & Results. These
# tests cover what AppTest *can* exercise headlessly: the gate blocking
# access, switching between the two screens, both forms' submission, and
# not blocking access once a session exists.

def _register_screen() -> AppTest:
    at = AppTest.from_file(APP_PATH).run()
    toggle_button = next(b for b in at.button if "Register" in b.label and b.label != "Register")
    return toggle_button.click().run()


def test_unauthenticated_user_sees_only_the_login_screen_not_the_app():
    at = AppTest.from_file(APP_PATH).run()
    assert not at.exception
    assert not at.number_input  # _require_login() st.stop()s before the rest of the app ever renders
    assert "Talent Matching Platform" in at.title[0].value
    assert len(at.text_input) == 2
    assert any(b.label == "Log in" for b in at.button)
    assert any(b.label == "New here? Register" for b in at.button)
    assert not any(ti.label == "Full name" for ti in at.text_input)


def test_clicking_register_toggle_shows_only_the_registration_screen():
    at = _register_screen()
    assert not at.exception
    assert len(at.text_input) == 4
    assert [ti.label for ti in at.text_input] == ["Full name", "Email", "Password", "Confirm password"]
    assert any(b.label == "Register" for b in at.button)
    assert any(b.label == "Already have an account? Log in" for b in at.button)
    assert not any(ti.label == "Username" for ti in at.text_input)


def test_clicking_log_in_toggle_from_registration_returns_to_login_screen():
    at = _register_screen()
    back_button = next(b for b in at.button if "Log in" in b.label and b.label != "Log in")
    at = back_button.click().run()
    assert not at.exception
    assert len(at.text_input) == 2
    assert any(b.label == "Log in" for b in at.button)


@patch("app.frontend.profile_client.get_profile")
@patch("app.frontend.oidc.requests.post")
def test_submitting_valid_login_credentials_logs_in(mock_token_post, mock_get_profile):
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
def test_submitting_invalid_login_credentials_shows_error_and_stays_on_login(mock_token_post):
    mock_token_post.return_value = MagicMock(status_code=401)
    at = AppTest.from_file(APP_PATH).run()
    at.text_input[0].set_value("candidate").run()
    at.text_input[1].set_value("wrong-password").run()
    login_button = next(b for b in at.button if b.label == "Log in")
    at = login_button.click().run()

    assert not at.exception
    assert "id_token_claims" not in at.session_state
    assert any("Invalid username or password" in e.value for e in at.error)


def test_submitting_registration_form_with_mismatched_passwords_shows_error():
    at = _register_screen()
    at.text_input[0].set_value("Ada Lovelace").run()
    at.text_input[1].set_value("ada@example-synthetic.test").run()
    at.text_input[2].set_value("s3cret-pw").run()
    at.text_input[3].set_value("different-pw").run()
    register_button = next(b for b in at.button if b.label == "Register")
    at = register_button.click().run()

    assert not at.exception
    assert "id_token_claims" not in at.session_state
    assert any("do not match" in e.value for e in at.error)


@patch("app.frontend.profile_client.get_profile")
@patch("app.frontend.oidc.requests.post")
@patch("app.frontend.keycloak_admin.create_user")
@patch("app.frontend.keycloak_admin.get_admin_token")
def test_submitting_registration_form_creates_user_and_logs_in(
    mock_get_token, mock_create_user, mock_token_post, mock_get_profile,
):
    mock_get_token.return_value = "fake-admin-token"
    mock_create_user.return_value = "user-newuser"
    mock_token_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"id_token": _fake_id_token("ada@example-synthetic.test", sub="user-newuser")},
    )
    mock_get_profile.return_value = None
    at = _register_screen()
    at.text_input[0].set_value("Ada Lovelace").run()
    at.text_input[1].set_value("ada@example-synthetic.test").run()
    at.text_input[2].set_value("s3cret-pw").run()
    at.text_input[3].set_value("s3cret-pw").run()
    register_button = next(b for b in at.button if b.label == "Register")
    at = register_button.click().run()

    assert not at.exception
    mock_create_user.assert_called_once_with(
        "ada@example-synthetic.test", "Ada Lovelace", "s3cret-pw", "fake-admin-token",
    )
    assert at.session_state["pending_full_name"] == "Ada Lovelace"
    # Past the login gate now, straight into the first-login profile form,
    # pre-filled with the name just entered at registration.
    assert any("Tell us a bit about yourself" in s.value for s in at.subheader)
    assert at.text_input[0].value == "Ada Lovelace"


@patch("app.frontend.keycloak_admin.create_user")
@patch("app.frontend.keycloak_admin.get_admin_token")
def test_submitting_registration_form_with_duplicate_email_shows_error(mock_get_token, mock_create_user):
    mock_get_token.return_value = "fake-admin-token"
    mock_create_user.side_effect = ValueError("An account with that email already exists.")
    at = _register_screen()
    at.text_input[0].set_value("Ada Lovelace").run()
    at.text_input[1].set_value("ada@example-synthetic.test").run()
    at.text_input[2].set_value("s3cret-pw").run()
    at.text_input[3].set_value("s3cret-pw").run()
    register_button = next(b for b in at.button if b.label == "Register")
    at = register_button.click().run()

    assert not at.exception
    assert "id_token_claims" not in at.session_state
    assert any("already exists" in e.value for e in at.error)


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
@patch("requests.get")
@patch("app.frontend.keycloak_admin.assign_realm_role")
@patch("app.frontend.keycloak_admin.get_admin_token")
def test_submitting_profile_form_as_candidate_assigns_role_and_shows_candidate_flow(
    mock_get_token, mock_assign_role, mock_get, mock_get_profile, mock_create_profile,
):
    mock_get_token.return_value = "fake-admin-token"
    mock_get_profile.return_value = None
    mock_create_profile.return_value = {"role": "candidate", "full_name": "Ada Lovelace", "candidate_id": 99}
    mock_get.side_effect = _no_existing_cv
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
    assert any("Upload your CV" in s.value for s in at.subheader)


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


@patch("requests.get")
def test_logged_in_user_sees_the_real_app_not_the_login_link(mock_get):
    mock_get.side_effect = _no_existing_cv
    at = _logged_in_app().run()
    assert not at.exception
    assert "Talent Matching Platform" in at.title[0].value
    assert not any(b.label == "Log in" for b in at.button)
    assert any("candidate" in b.label for b in at.sidebar.button)


@patch("requests.get")
def test_logout_button_clears_session_and_shows_login_link_again(mock_get):
    mock_get.side_effect = _no_existing_cv
    at = _logged_in_app().run()
    logout_button = next(b for b in at.sidebar.button if "Log out" in b.label)
    at = logout_button.click().run()
    assert not at.exception
    assert any(b.label == "Log in" for b in at.button)
