"""Minimal end-to-end web frontend: a thin UI over the existing
matching/extraction APIs.

Users register and log in on this own page as two separate screens (a
"New here? Register"/"Already have an account?" toggle switches between
them) — no redirect to a separate Keycloak-hosted page for either one —
then fill in a one-time profile form (name, candidate/employer role,
company name) on first login (issue #15/#16), but candidate/job *records*
are still the already-seeded synthetic dataset, not something users create
here: a candidate picks an existing candidate_id, an employer picks an
existing job_id. See docs/architecture/frontend.md.
"""
import os

import requests
import streamlit as st

from app.common.logging import setup_logging
from app.frontend.keycloak_admin import assign_realm_role, create_user, get_admin_token
from app.frontend.oidc import decode_id_token, password_grant_login
from app.frontend.profile_client import create_profile, get_profile

setup_logging("frontend")

# routed through the API Gateway (docker/nginx-gateway/nginx.conf),
# not called directly — api/extraction still see identical paths, the
# Gateway strips its routing prefix. See docs/architecture/ingress-and-identity.md.
API_URL = os.environ.get("API_URL", "http://gateway/matching")
EXTRACTION_URL = os.environ.get("EXTRACTION_URL", "http://gateway/extraction")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
HEADERS = {"X-API-Key": API_KEY}
MAX_CV_SIZE_BYTES = 2 * 1024 * 1024


def _render_login_screen() -> None:
    st.title("Talent Matching Platform")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password", key="login_password")
    if st.button("Log in"):
        try:
            token_json = password_grant_login(username, password)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["id_token_claims"] = decode_id_token(token_json["id_token"])
            st.rerun()

    if st.button("New here? Register"):
        st.session_state["auth_view"] = "register"
        st.rerun()


def _render_registration_screen() -> None:
    st.title("Talent Matching Platform")
    full_name = st.text_input("Full name")
    email = st.text_input("Email")
    new_password = st.text_input("Password", type="password", key="register_password")
    confirm_password = st.text_input("Confirm password", type="password", key="register_password_confirm")
    if st.button("Register"):
        if new_password != confirm_password:
            st.error("Passwords do not match.")
        else:
            try:
                admin_token = get_admin_token()
                create_user(email, full_name, new_password, admin_token)
                token_json = password_grant_login(email, new_password)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["id_token_claims"] = decode_id_token(token_json["id_token"])
                st.session_state["pending_full_name"] = full_name
                st.rerun()

    if st.button("Already have an account? Log in"):
        st.session_state["auth_view"] = "login"
        st.rerun()


def _require_login() -> None:
    """Both login and registration happen inline on this page as separate
    screens (toggled via `session_state["auth_view"]`) — no redirect to a
    separate Keycloak-hosted page. Login is a Resource Owner Password grant
    (`password_grant_login` in oidc.py); registration creates the user
    directly via the Keycloak Admin API (`create_user` in keycloak_admin.py)
    and then immediately logs them in the same way. See
    docs/architecture/ingress-and-identity.md Known Limitations for the
    tradeoff this accepts (the app itself handles the raw password, rather
    than never seeing it).
    """
    if "id_token_claims" in st.session_state:
        return

    st.set_page_config(page_title="Project JME", page_icon="🧩")

    if st.session_state.get("auth_view") == "register":
        _render_registration_screen()
    else:
        _render_login_screen()

    st.stop()


def _require_profile() -> str:
    """Returns the user's candidate/employer role. The app's own user_profiles
    table (issue #15, app/api/profile_routes.py) is the source of truth for
    role here, not the Keycloak realm-role claim: a user with no profile yet
    (brand-new self-registration, issue #14) is shown a one-time form asking
    for their name, a candidate/employer choice, and (for employers) a
    company name. Submitting it both creates the profile row and assigns the
    matching Keycloak realm role via the Admin API (keycloak_admin.py, the
    jme-admin service account — least-privilege, separate from the end-user
    OIDC login above) so the realm-role claim stays available for any future
    backend-enforced authorization, even though the UI itself now reads role
    from Postgres."""
    if "profile" in st.session_state:
        return st.session_state["profile"]["role"]

    claims = st.session_state["id_token_claims"]
    profile = get_profile(claims["sub"])
    if profile is not None:
        st.session_state["profile"] = profile
        return profile["role"]

    st.title("Talent Matching Platform")
    st.subheader("Welcome! Tell us a bit about yourself.")
    default_name = claims.get("name") or st.session_state.get("pending_full_name", "")
    full_name = st.text_input("Full name", value=default_name)
    chosen_role = st.radio("I am a...", ["Candidate", "Employer"], horizontal=True)
    company_name = None
    if chosen_role == "Employer":
        company_name = st.text_input("Company name")

    if st.button("Continue"):
        role = chosen_role.lower()
        admin_token = get_admin_token()
        assign_realm_role(claims["sub"], role, admin_token)
        profile = create_profile(
            keycloak_user_id=claims["sub"],
            role=role,
            full_name=full_name,
            email=claims.get("email", ""),
            company_name=company_name,
        )
        st.session_state["profile"] = profile
        st.rerun()
    st.stop()


_require_login()

st.set_page_config(page_title="Talent Matching Platform", page_icon="🧩", layout="wide")
role = _require_profile()

st.title("Talent Matching Platform")
st.caption(
    "A thin demo UI over the real matching/extraction APIs — reuses the existing synthetic "
    "dataset for candidate/job records, see docs/architecture/frontend.md for scope and limitations."
)
username = st.session_state["id_token_claims"].get("preferred_username", "?")
if st.sidebar.button(f"Log out ({username})"):
    st.session_state.clear()
    st.rerun()

st.sidebar.caption(f"Signed in as {role}.")


def _render_matches(matches: list[dict]) -> None:
    if not matches:
        st.info("No eligible matches found.")
        return
    rows = [
        {
            "ID": m["entity_id"],
            "Label": m["label"],
            "Eligible": m["eligible"],
            "Score": m["score"]["total"],
            "Explanation": m["explanation"],
        }
        for m in matches
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


if role == "candidate":
    st.header("Candidate")
    candidate_id = st.number_input("Candidate ID (from the seeded dataset)", min_value=1, value=1, step=1)

    st.subheader("1. Upload your CV")
    cv_file = st.file_uploader("PDF only, 2MB max", type=["pdf"])
    if st.button("Upload CV", disabled=cv_file is None):
        data = cv_file.getvalue()
        if len(data) > MAX_CV_SIZE_BYTES:
            st.error(f"File too large: {len(data)} bytes exceeds the {MAX_CV_SIZE_BYTES}-byte cap.")
        else:
            response = requests.post(
                f"{EXTRACTION_URL}/candidates/{candidate_id}/cv",
                headers=HEADERS,
                files={"file": (cv_file.name, data, "application/pdf")},
                timeout=10,
            )
            if response.status_code == 201:
                st.success(f"CV uploaded: {response.json()['original_filename']}")
            else:
                st.error(f"Upload failed ({response.status_code}): {response.text}")

    st.subheader("2. Your job recommendations")
    if st.button("Get My Job Matches"):
        response = requests.get(
            f"{API_URL}/candidates/{candidate_id}/job-matches", headers=HEADERS, timeout=10,
        )
        if response.status_code == 200:
            st.caption(f"Ranking mode: {response.headers.get('X-Ranking-Mode', 'unknown')}")
            _render_matches(response.json())
        else:
            st.error(f"Request failed ({response.status_code}): {response.text}")

else:
    st.header("Employer")
    job_id = st.number_input("Job ID (from the seeded dataset)", min_value=1, value=1, step=1)

    st.subheader("Matching candidates")
    if st.button("Get Matching Candidates"):
        response = requests.get(
            f"{API_URL}/jobs/{job_id}/candidate-matches", headers=HEADERS, timeout=10,
        )
        if response.status_code == 200:
            _render_matches(response.json())
        else:
            st.error(f"Request failed ({response.status_code}): {response.text}")
