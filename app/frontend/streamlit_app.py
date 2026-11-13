"""Minimal end-to-end web frontend: a thin UI over the existing
matching/extraction APIs.

Users register and log in on this own page as two separate screens (a
"New here? Register"/"Already have an account?" toggle switches between
them) — no redirect to a separate Keycloak-hosted page for either one —
then fill in a one-time profile form (name, candidate/employer role,
company name) on first login (issue #15/#16), but candidate/job *records*
are still the already-seeded synthetic dataset, not something users create
here: a candidate picks an existing candidate_id, an employer picks an
existing job_id. Candidates can also edit the scoring-relevant fields the
matching engine reads (location, salary, occupation, skills) via "Your
profile" (issue #18). See docs/architecture/frontend.md.
"""
import os

import requests
import streamlit as st

from app.common.logging import setup_logging
from app.frontend.keycloak_admin import assign_realm_role, create_user, get_admin_token
from app.frontend.oidc import decode_id_token, password_grant_login
from app.frontend.candidate_client import (
    create_occupation,
    create_skill,
    get_candidate_details,
    list_occupations,
    list_skills,
    update_candidate_details,
)
from app.frontend.job_client import create_job, get_job_details, list_employer_jobs, update_job_details
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
    # Issue #17: a real, personal candidate_id created for this user at
    # registration time (app/api/profile_routes.py's _get_or_create_candidate),
    # linked on their own user_profiles row — no longer a hardcoded id shared
    # by every candidate.
    candidate_id = st.session_state["profile"]["candidate_id"]

    st.subheader("Upload your CV")
    existing_cv = requests.get(f"{EXTRACTION_URL}/candidates/{candidate_id}/cv", headers=HEADERS, timeout=10)
    if existing_cv.status_code == 200:
        cv_info = existing_cv.json()
        st.info(
            f"Current CV on file: **{cv_info['original_filename']}** "
            f"({cv_info['size_bytes']} bytes, uploaded {cv_info['uploaded_at']})"
        )
        uploader_label = "Replace your CV (PDF only, 2MB max)"
    else:
        uploader_label = "PDF only, 2MB max"

    cv_file = st.file_uploader(uploader_label, type=["pdf"])
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
                st.rerun()
            else:
                st.error(f"Upload failed ({response.status_code}): {response.text}")

    st.subheader("Your profile")
    details = get_candidate_details(candidate_id)
    occupations = list_occupations()
    skills = list_skills()
    skill_name_by_id = {s["id"]: s["name"] for s in skills}

    city_col, postal_col, country_col = st.columns(3)
    with city_col:
        location_city = st.text_input("City", value=details["location_city"])
    with postal_col:
        postal_code = st.text_input("Postal code", value=details["postal_code"] or "")
    with country_col:
        country = st.text_input("Country", value=details["country"] or "")

    salary_col1, salary_col2 = st.columns(2)
    with salary_col1:
        salary_min = st.number_input(
            "Desired salary (min) ($)", min_value=0, step=1000,
            value=details["desired_salary_min"] or 0,
        )
    with salary_col2:
        salary_max = st.number_input(
            "Desired salary (max) ($)", min_value=0, step=1000,
            value=details["desired_salary_max"] or 0,
        )

    EXPERIENCE_LEVELS = {
        "Entry Level": "entry_level", "Mid-Level": "mid_level",
        "Senior": "senior", "Expert": "expert",
    }
    level_labels = list(EXPERIENCE_LEVELS.keys())
    level_by_value = {v: k for k, v in EXPERIENCE_LEVELS.items()}
    current_level_label = level_by_value.get(details["experience_level"], level_labels[0])
    experience_label = st.selectbox(
        "Experience level", level_labels, index=level_labels.index(current_level_label),
    )

    ADD_NEW_OCCUPATION = "+ Add a new occupation"
    occupation_titles = [o["title"] for o in occupations] + [ADD_NEW_OCCUPATION]
    current_occupation = next(
        (o["title"] for o in occupations if o["id"] == details["primary_occupation_id"]), None,
    )
    occupation_title = st.selectbox(
        "Occupation", occupation_titles,
        index=occupation_titles.index(current_occupation) if current_occupation in occupation_titles else 0,
    )
    new_occupation_title = ""
    if occupation_title == ADD_NEW_OCCUPATION:
        new_occupation_title = st.text_input("New occupation title")

    skill_options = [s["name"] for s in skills]
    default_skill_names = [skill_name_by_id[sid] for sid in details["skill_ids"] if sid in skill_name_by_id]
    chosen_skill_names = st.multiselect(
        "Your top skills (up to 10)", skill_options, default=default_skill_names, max_selections=10,
    )
    new_skill_name = st.text_input("Add a new skill (optional)")

    if st.button("Save profile details"):
        skill_id_by_name = {s["name"]: s["id"] for s in skills}
        occupation_id_by_title = {o["title"]: o["id"] for o in occupations}

        if occupation_title == ADD_NEW_OCCUPATION and new_occupation_title.strip():
            occupation_id = create_occupation(new_occupation_title.strip())["id"]
        else:
            occupation_id = occupation_id_by_title.get(occupation_title)

        skill_ids = [skill_id_by_name[name] for name in chosen_skill_names]
        if new_skill_name.strip():
            new_skill_id = create_skill(new_skill_name.strip())["id"]
            if new_skill_id not in skill_ids:
                skill_ids.append(new_skill_id)

        update_candidate_details(
            candidate_id,
            location_city=location_city,
            postal_code=postal_code or None,
            country=country or None,
            desired_salary_min=salary_min or None,
            desired_salary_max=salary_max or None,
            primary_occupation_id=occupation_id,
            experience_level=EXPERIENCE_LEVELS[experience_label],
            skill_ids=skill_ids,
        )
        st.success("Profile details saved.")
        st.rerun()

    if st.button("Show Job Matches"):
        response = requests.get(
            f"{API_URL}/candidates/{candidate_id}/job-matches",
            headers=HEADERS, params={"top_k": 10}, timeout=10,
        )
        if response.status_code == 200:
            st.caption(f"Ranking mode: {response.headers.get('X-Ranking-Mode', 'unknown')}")
            _render_matches(response.json())
        else:
            st.error(f"Request failed ({response.status_code}): {response.text}")

else:
    # Issue #22: an employer's own job postings, created/edited here instead
    # of picking a raw job_id from the seeded dataset. Ownership is tracked
    # by Job.posted_by_keycloak_user_id -- see app/api/job_routes.py.
    st.header("Employer")
    keycloak_user_id = st.session_state["id_token_claims"]["sub"]
    jobs = list_employer_jobs(keycloak_user_id)
    occupations = list_occupations()
    skills = list_skills()
    skill_name_by_id = {s["id"]: s["name"] for s in skills}

    POST_NEW_JOB = "+ Post a new job"
    job_label_by_id = {j["id"]: f"{j['title']} — {j['company_name']}" for j in jobs}
    job_labels = list(job_label_by_id.values()) + [POST_NEW_JOB]
    job_id_by_label = {label: job_id for job_id, label in job_label_by_id.items()}

    default_index = 0
    selected_label = job_label_by_id.get(st.session_state.get("selected_job_id"))
    if selected_label in job_labels:
        default_index = job_labels.index(selected_label)
    chosen_job_label = st.selectbox("Your jobs", job_labels, index=default_index)

    if chosen_job_label == POST_NEW_JOB:
        job_id = None
        details = {
            "title": "", "company_name": st.session_state["profile"].get("company_name") or "",
            "description": "", "location_city": "", "postal_code": "", "country": "",
            "salary_min": None, "salary_max": None, "primary_occupation_id": None,
            "experience_level": "entry_level", "required_skill_ids": [], "preferred_skill_ids": [],
        }
    else:
        job_id = job_id_by_label[chosen_job_label]
        details = get_job_details(job_id)

    st.subheader("Job details")
    title = st.text_input("Job title", value=details["title"])
    company_name = st.text_input("Company name", value=details["company_name"])
    description = st.text_area("Job description", value=details["description"])

    job_city_col, job_postal_col, job_country_col = st.columns(3)
    with job_city_col:
        location_city = st.text_input("City", value=details["location_city"], key="job_city")
    with job_postal_col:
        postal_code = st.text_input("Postal code", value=details["postal_code"] or "", key="job_postal_code")
    with job_country_col:
        country = st.text_input("Country", value=details["country"] or "", key="job_country")

    salary_col1, salary_col2 = st.columns(2)
    with salary_col1:
        salary_min = st.number_input(
            "Salary (min) ($)", min_value=0, step=1000, value=details["salary_min"] or 0, key="job_salary_min",
        )
    with salary_col2:
        salary_max = st.number_input(
            "Salary (max) ($)", min_value=0, step=1000, value=details["salary_max"] or 0, key="job_salary_max",
        )

    JOB_EXPERIENCE_LEVELS = {
        "Entry Level": "entry_level", "Mid-Level": "mid_level",
        "Senior": "senior", "Expert": "expert",
    }
    job_level_labels = list(JOB_EXPERIENCE_LEVELS.keys())
    job_level_by_value = {v: k for k, v in JOB_EXPERIENCE_LEVELS.items()}
    current_job_level_label = job_level_by_value.get(details["experience_level"], job_level_labels[0])
    experience_label = st.selectbox(
        "Minimum experience required", job_level_labels,
        index=job_level_labels.index(current_job_level_label), key="job_experience",
    )

    JOB_ADD_NEW_OCCUPATION = "+ Add a new occupation"
    job_occupation_titles = [o["title"] for o in occupations] + [JOB_ADD_NEW_OCCUPATION]
    current_job_occupation = next(
        (o["title"] for o in occupations if o["id"] == details["primary_occupation_id"]), None,
    )
    job_occupation_title = st.selectbox(
        "Occupation", job_occupation_titles,
        index=job_occupation_titles.index(current_job_occupation)
        if current_job_occupation in job_occupation_titles else 0,
        key="job_occupation",
    )
    new_job_occupation_title = ""
    if job_occupation_title == JOB_ADD_NEW_OCCUPATION:
        new_job_occupation_title = st.text_input("New occupation title", key="job_new_occupation")

    skill_options = [s["name"] for s in skills]
    default_required_names = [
        skill_name_by_id[sid] for sid in details["required_skill_ids"] if sid in skill_name_by_id
    ]
    default_preferred_names = [
        skill_name_by_id[sid] for sid in details["preferred_skill_ids"] if sid in skill_name_by_id
    ]
    chosen_required_names = st.multiselect(
        "Required skills (up to 10)", skill_options, default=default_required_names,
        max_selections=10, key="job_required_skills",
    )
    chosen_preferred_names = st.multiselect(
        "Preferred skills (up to 10)", skill_options, default=default_preferred_names,
        max_selections=10, key="job_preferred_skills",
    )
    new_job_skill_name = st.text_input("Add a new skill (optional)", key="job_new_skill")

    if st.button("Save job"):
        skill_id_by_name = {s["name"]: s["id"] for s in skills}
        occupation_id_by_title = {o["title"]: o["id"] for o in occupations}

        if job_occupation_title == JOB_ADD_NEW_OCCUPATION and new_job_occupation_title.strip():
            occupation_id = create_occupation(new_job_occupation_title.strip())["id"]
        else:
            occupation_id = occupation_id_by_title.get(job_occupation_title)

        required_skill_ids = [skill_id_by_name[name] for name in chosen_required_names]
        preferred_skill_ids = [skill_id_by_name[name] for name in chosen_preferred_names]
        if new_job_skill_name.strip():
            new_skill_id = create_skill(new_job_skill_name.strip())["id"]
            if new_skill_id not in required_skill_ids and new_skill_id not in preferred_skill_ids:
                required_skill_ids.append(new_skill_id)

        if job_id is None:
            saved = create_job(
                keycloak_user_id, title, company_name, description, location_city,
                postal_code or None, country or None,
                salary_min or None, salary_max or None, occupation_id,
                JOB_EXPERIENCE_LEVELS[experience_label], required_skill_ids, preferred_skill_ids,
            )
        else:
            saved = update_job_details(
                job_id, title, company_name, description, location_city,
                postal_code or None, country or None,
                salary_min or None, salary_max or None, occupation_id,
                JOB_EXPERIENCE_LEVELS[experience_label], required_skill_ids, preferred_skill_ids,
            )
        st.success("Job saved.")
        st.session_state["selected_job_id"] = saved["id"]
        st.rerun()

    st.subheader("Matching candidates")
    if job_id is not None and st.button("Get Matching Candidates"):
        response = requests.get(
            f"{API_URL}/jobs/{job_id}/candidate-matches", headers=HEADERS, timeout=10,
        )
        if response.status_code == 200:
            _render_matches(response.json())
        else:
            st.error(f"Request failed ({response.status_code}): {response.text}")
