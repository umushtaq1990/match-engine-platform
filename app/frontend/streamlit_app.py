"""Minimal end-to-end web frontend (Phase 27): a thin UI over the existing
matching/extraction APIs, nothing more. Deliberately basic per the brief —
no auth, no new backend logic, no candidate/job creation — this closes the
one real gap every prior phase left open: the whole platform was API-only,
with no way for a human to actually click through the flow it's built for.

Reuses the already-seeded synthetic dataset rather than registration: a
candidate picks an existing candidate_id, an employer picks an existing
job_id. See docs/frontend.md.
"""
import os

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")
EXTRACTION_URL = os.environ.get("EXTRACTION_URL", "http://extraction:8002")
API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
HEADERS = {"X-API-Key": API_KEY}
MAX_CV_SIZE_BYTES = 2 * 1024 * 1024

st.set_page_config(page_title="Project JME", page_icon="🧩", layout="wide")
st.title("Project JME — Talent Matching Platform")
st.caption(
    "A thin demo UI over the real matching/extraction APIs — reuses the existing synthetic "
    "dataset (no registration), see docs/frontend.md for scope and limitations."
)

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
