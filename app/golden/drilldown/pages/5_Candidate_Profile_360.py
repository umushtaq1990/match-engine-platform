"""Candidate Profile 360: the "zoom all the way in" complement to page 4's
population-level Skill Clusters 360. Pick one golden candidate and see,
in one place: who they are (name/location/years of experience -- no age
or photo field exists anywhere in this schema, confirmed; a decorative
initials avatar stands in for a photo, colored by their skill cluster),
what they actually know (their own `candidate_skills`), how they score
against the real matching engine (app/matching/rank.py, unmodified, same
call the production API uses), who's most similar to them by skills
embedding (a genuinely new capability -- no candidate-to-candidate
similarity existed anywhere in this repo before this page), and two
skill-gap views built the same way over two different populations:
rank skills by how many of the ~10 similar candidates (or top matching
jobs, via each job's real required-skill set from the matching engine's
own ScoreBreakdown) have/require them, subtract skills this candidate
already has, and whatever's left is the gap. See
docs/architecture/data-platform/skill-clustering.md.
"""
import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import select

from app.db import SessionLocal
from app.golden.drilldown.chart import render_top_skills_bar_chart
from app.golden.drilldown.data import (
    compute_job_skill_gap,
    compute_peer_skill_gap,
    find_similar_candidates,
    load_behavioral_rows,
    load_candidate_demographics,
    load_candidate_skill_detail,
    load_candidate_skill_names,
    load_job_occupation_titles,
    load_skill_cluster_rows,
    name_clusters_by_majority,
)
from app.golden.select import select_existing_golden_candidate_ids
from app.ingestion.source_models import Skill
from app.matching.rank import rank_jobs_for_candidate

st.set_page_config(page_title="Candidate Profile 360", layout="wide")
st.title("Candidate Profile 360 -- one person, end to end")

session = SessionLocal()
try:
    cluster_rows = load_skill_cluster_rows(session)
    behavioral_rows = load_behavioral_rows(session)
    golden_ids = select_existing_golden_candidate_ids(session)
    demographics = load_candidate_demographics(session, golden_ids)
finally:
    session.close()

if cluster_rows.empty:
    st.warning(
        "No candidate_skill_clusters rows found for the golden candidates -- "
        "run `docker compose run --rm cluster-nightly` first."
    )
    st.stop()

cluster_rows["skill_cluster_name"] = cluster_rows["cluster_label"].map(
    name_clusters_by_majority(cluster_rows, "cluster_label", "domain")
)
profile_rows = (
    cluster_rows.merge(demographics, on="candidate_id", how="left")
    .merge(behavioral_rows[["candidate_id", "segment", "persona"]], on="candidate_id", how="left")
)

candidate_options = {
    f"{row.candidate_id} -- {row.full_name}": row.candidate_id
    for row in profile_rows.sort_values("candidate_id").itertuples()
}
choice_label = st.selectbox("Select a candidate", list(candidate_options.keys()))
candidate_id = candidate_options[choice_label]
profile = profile_rows.loc[profile_rows["candidate_id"] == candidate_id].iloc[0]

_CLUSTER_PALETTE = px.colors.qualitative.Plotly
_cluster_index = sorted(profile_rows["cluster_label"].unique()).index(profile["cluster_label"])
_avatar_color = _CLUSTER_PALETTE[_cluster_index % len(_CLUSTER_PALETTE)]
_initials = "".join(part[0].upper() for part in profile["full_name"].split()[:2]) or "?"

header_left, header_right = st.columns([1, 5])
with header_left:
    st.markdown(
        f"""<div style="width:90px;height:90px;border-radius:50%;background:{_avatar_color};
        display:flex;align-items:center;justify-content:center;color:white;font-size:32px;
        font-weight:bold;">{_initials}</div>""",
        unsafe_allow_html=True,
    )
with header_right:
    st.markdown(f"### {profile['full_name']}")
    st.caption(f"Candidate #{candidate_id}")
    info_cols = st.columns(4)
    info_cols[0].metric("Location", f"{profile['location_city']}, {profile['location_region']}")
    info_cols[1].metric("Years of experience", int(profile["years_experience"]))
    info_cols[2].metric("Domain", profile["domain"])
    info_cols[3].metric("Occupation", profile["occupation"])
    info_cols = st.columns(4)
    info_cols[0].metric("Skill cluster", profile["skill_cluster_name"])
    info_cols[1].metric("Louvain community", profile["louvain_community_id"])
    info_cols[2].metric("Segment", profile["segment"] or "unknown")
    info_cols[3].metric("Persona", profile["persona"] or "unknown")

st.markdown("#### Skills")
session = SessionLocal()
try:
    skills_detail = load_candidate_skill_detail(session, candidate_id)
finally:
    session.close()
st.dataframe(skills_detail, use_container_width=True, hide_index=True)

st.markdown("#### Matching jobs")
st.caption(
    "The real matching engine (app/matching/rank.py), unmodified, called the same way the "
    "production API does -- against the full job pool, not just golden jobs."
)
session = SessionLocal()
try:
    matches = rank_jobs_for_candidate(session, candidate_id, top_k=10)
    occupation_titles = load_job_occupation_titles(session, [m.entity_id for m in matches])
    skill_names = session.execute(select(Skill.id, Skill.name)).all()
finally:
    session.close()
skill_name_by_id = dict(skill_names)

if not matches:
    st.info("No eligible matching jobs found for this candidate.")
else:
    match_rows = []
    for m in matches:
        missing_required = m.skill_gap.confirmed_absent_required | m.skill_gap.not_recorded_required
        match_rows.append({
            "job_id": m.entity_id,
            "title": m.label,
            "occupation": occupation_titles.get(m.entity_id, "unknown"),
            "total_score": round(m.score.total, 3),
            "skill_score": round(m.score.skill, 3),
            "location_score": round(m.score.location, 3),
            "salary_score": round(m.score.salary, 3),
            "occupation_score": round(m.score.occupation, 3),
            "missing_required_skills": ", ".join(
                sorted(skill_name_by_id.get(i, f"#{i}") for i in missing_required)
            ) or "(none)",
        })
    st.dataframe(pd.DataFrame(match_rows), use_container_width=True, hide_index=True)

st.markdown("#### Similar candidates")
st.caption(
    "Nearest neighbors by skills-embedding distance (new -- no candidate-to-candidate similarity "
    "existed anywhere in this repo before this page), with same-cluster/same-Louvain-community "
    "flags alongside as a cross-check against the graph-based notion of similarity."
)
similar = find_similar_candidates(cluster_rows, candidate_id, top_n=10)
similar = similar.merge(demographics, on="candidate_id", how="left").merge(
    cluster_rows[["candidate_id", "domain", "occupation"]], on="candidate_id", how="left"
)
st.dataframe(
    similar[[
        "candidate_id", "full_name", "domain", "occupation",
        "distance", "same_skill_cluster", "same_louvain_community",
    ]],
    use_container_width=True, hide_index=True,
)

st.markdown("#### Skill gap")
st.caption(
    "Same method, two independent populations: rank skills by how many of the ~10 similar "
    "candidates (or top matching jobs) have/require them, subtract skills this candidate "
    "already has, and whatever's left -- ranked by that popularity -- is the gap."
)
target_skill_names = set(skills_detail["skill_name"])
gap_left, gap_right = st.columns(2)
with gap_left:
    st.markdown("##### vs. similar candidates")
    session = SessionLocal()
    try:
        peer_skill_rows = load_candidate_skill_names(session, similar["candidate_id"].tolist())
    finally:
        session.close()
    peer_gap = compute_peer_skill_gap(target_skill_names, peer_skill_rows, peer_count=len(similar))
    if peer_gap.empty:
        st.info("This candidate already has every skill their similar candidates have.")
    else:
        render_top_skills_bar_chart(
            peer_gap, "Skills peers have that this candidate doesn't", height=350
        )
with gap_right:
    st.markdown("##### vs. top matching jobs")
    if not matches:
        st.info("No matching jobs to compare against.")
    else:
        job_required_skill_names = [
            {
                skill_name_by_id.get(i, f"#{i}")
                for i in (m.score.matching_required_skill_ids | m.score.missing_required_skill_ids)
            }
            for m in matches
        ]
        job_gap = compute_job_skill_gap(target_skill_names, job_required_skill_names)
        if job_gap.empty:
            st.info("This candidate already has every required skill across their top matching jobs.")
        else:
            render_top_skills_bar_chart(
                job_gap, "Required skills missing from top matching jobs", height=350
            )
