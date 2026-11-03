"""Minimal end-to-end web frontend (Phase 27): a thin UI over the existing
matching/extraction APIs, nothing more. Deliberately basic per the brief —
no auth, no new backend logic, no candidate/job creation — this closes the
one real gap every prior phase left open: the whole platform was API-only,
with no way for a human to actually click through the flow it's built for.

Reuses the already-seeded synthetic dataset rather than registration: a
candidate picks an existing candidate_id, an employer picks an existing
job_id. See docs/architecture/frontend.md.
"""
import base64
import json
import os
import secrets
import time

import requests
import streamlit as st

from app.common.logging import setup_logging

setup_logging("frontend")

# Phase 28: routed through the API Gateway (docker/nginx-gateway/nginx.conf),
# not called directly — api/extraction still see identical paths, the
# Gateway strips its routing prefix. See docs/architecture/ingress-and-identity.md.
API_URL = os.environ.get("API_URL", "http://gateway/matching")
EXTRACTION_URL = os.environ.get("EXTRACTION_URL", "http://gateway/extraction")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
HEADERS = {"X-API-Key": API_KEY}
MAX_CV_SIZE_BYTES = 2 * 1024 * 1024

# Phase 29: OIDC login gate (Keycloak). Two issuer URLs: the browser follows
# the public one, token exchange happens server-side against the internal
# one — see docs/architecture/ingress-and-identity.md for why both are needed. This is a human/browser
# login gate only; JME_API_KEY above is unchanged and still what the APIs
# themselves check.
OIDC_CLIENT_ID = "jme-frontend"
OIDC_PUBLIC_ISSUER = os.environ.get("OIDC_PUBLIC_ISSUER", "http://localhost:8180/realms/jme")
OIDC_INTERNAL_ISSUER = os.environ.get("OIDC_INTERNAL_ISSUER", "http://keycloak:8080/realms/jme")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "http://localhost:8080/")

# A real bug, found by actually clicking through the login in a browser (not
# just a scripted HTTP client): clicking the "Log in with SSO" link navigates
# the browser away to Keycloak's own page, and Keycloak's redirect back is a
# fresh pageload — Streamlit gives that a brand-new session with empty
# st.session_state, so a `state` value stored in st.session_state before the
# redirect is always gone by the time the callback arrives.
#
# A first fix attempt moved the state into a plain module-level dict, assuming
# that — since `frontend` runs as a single process — it would survive the
# round trip. It didn't: confirmed live (via temporary debug logging) that the
# dict was empty again on the very next check, immediately after being
# populated. Root cause: Streamlit re-executes the *entire script file*
# top-to-bottom on every rerun (that's its execution model), so a plain
# `= {}` module-level assignment resets the dict to empty on every single
# rerun, not just across the external redirect. `st.cache_resource` is
# Streamlit's actual mechanism for a value that survives reruns within one
# process — the factory function below only actually runs once; every rerun
# gets back the same dict object.
_OIDC_STATE_TTL_SECONDS = 600


@st.cache_resource(show_spinner=False)
def _pending_oidc_states() -> dict[str, float]:
    return {}


def _prune_stale_oidc_states() -> None:
    pending = _pending_oidc_states()
    cutoff = time.time() - _OIDC_STATE_TTL_SECONDS
    for stored_state, created_at in list(pending.items()):
        if created_at < cutoff:
            del pending[stored_state]


def _require_login() -> None:
    """Hand-rolled Authorization Code flow with `requests`, not a framework —
    matches this project's existing os.environ + requests convention (e.g.
    app/api/analytics_client.py) rather than pulling in authlib, which
    assumes a traditional request/response web framework Streamlit doesn't
    have. See docs/architecture/ingress-and-identity.md Known Limitations for
    what this does and doesn't protect (no PKCE, no signature verification on
    the ID token — fine for a local demo, not a hardened session
    architecture).
    """
    if "id_token_claims" in st.session_state:
        return

    params = st.query_params
    if "code" in params and "state" in params:
        returned_state = params.get("state")
        pending = _pending_oidc_states()
        if returned_state not in pending:
            st.error("OIDC state mismatch — possible CSRF, please log in again.")
            st.query_params.clear()
            st.stop()
        del pending[returned_state]
        token_resp = requests.post(
            f"{OIDC_INTERNAL_ISSUER}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "code": params["code"],
                "redirect_uri": OIDC_REDIRECT_URI,
                "client_id": OIDC_CLIENT_ID,
            },
            timeout=10,
        )
        st.query_params.clear()
        if token_resp.status_code != 200:
            st.error(f"Login failed: {token_resp.text}")
            st.stop()
        id_token = token_resp.json()["id_token"]
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        st.session_state["id_token_claims"] = json.loads(base64.urlsafe_b64decode(payload_b64))
        st.rerun()

    _prune_stale_oidc_states()
    state = secrets.token_urlsafe(16)
    _pending_oidc_states()[state] = time.time()
    auth_url = (
        f"{OIDC_PUBLIC_ISSUER}/protocol/openid-connect/auth"
        f"?client_id={OIDC_CLIENT_ID}&response_type=code&scope=openid"
        f"&redirect_uri={OIDC_REDIRECT_URI}&state={state}"
    )
    st.set_page_config(page_title="Project JME", page_icon="🧩")
    st.title("Project JME — Talent Matching Platform")
    st.markdown(f"### [Log in with SSO]({auth_url})")
    st.caption("Authentication via Keycloak. See docs/architecture/ingress-and-identity.md.")
    st.stop()


_require_login()

st.set_page_config(page_title="Project JME", page_icon="🧩", layout="wide")
st.title("Project JME — Talent Matching Platform")
st.caption(
    "A thin demo UI over the real matching/extraction APIs — reuses the existing synthetic "
    "dataset (no registration), see docs/architecture/frontend.md for scope and limitations."
)
username = st.session_state["id_token_claims"].get("preferred_username", "?")
if st.sidebar.button(f"Log out ({username})"):
    st.session_state.clear()
    st.rerun()

role = st.sidebar.radio("I am a...", ["Candidate", "Employer"])


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


if role == "Candidate":
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
