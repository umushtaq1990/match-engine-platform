"""Job Profile 360: the job-side mirror of page 5 (Candidate Profile 360)
-- the "zoom all the way in" complement to page 6's population-level Job
Skills Clustering 360. Pick one golden job and see, in one place: what
it is (title/company/location/salary -- a decorative initials avatar,
colored by its skill cluster, stands in next to it), what it requires
(its own `job_skills`), how it scores against the real matching engine
in reverse (app/matching/rank.py's `rank_candidates_for_job`,
unmodified, never used anywhere in this app before this page), which
jobs are most similar to it by required-skill embedding, and two
skill-gap views over two different populations: what similar jobs
require that this one doesn't, and -- the reverse direction of page 5's
job-skill-gap -- which required skills even this job's own best-matching
candidates commonly lack (straight from rank_candidates_for_job's own
SkillGapReport). Closes with a weekly engagement trend (real candidate
views/clicks/applications/saves on this job) against its own skill
cluster's average.

No market-signal/course-recommendation section here -- courses are for
people, not postings; that part of page 5 has no job equivalent and is
dropped, not faked. See docs/architecture/data-platform/skill-clustering.md.
"""
import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import select

from app.db import SessionLocal
from app.golden.drilldown.chart import render_top_skills_bar_chart
from app.golden.drilldown.data import (
    WEEK_BUCKET_LABELS,
    WEEK_BUCKET_ORDER,
    compute_job_engagement_trend_vs_cluster,
    compute_peer_skill_gap,
    compute_unmet_requirement_frequency,
    find_similar_entities,
    load_job_demographics,
    load_job_skill_cluster_rows,
    load_job_skill_detail,
    load_job_skill_names,
    name_clusters_by_majority,
)
from app.golden.select import select_existing_golden_job_ids
from app.ingestion.source_models import Skill
from app.matching.rank import rank_candidates_for_job

st.set_page_config(page_title="Job Profile 360", layout="wide")
st.title("Job Profile 360 -- one posting, end to end")

session = SessionLocal()
try:
    cluster_rows = load_job_skill_cluster_rows(session)
    golden_ids = select_existing_golden_job_ids(session)
    demographics = load_job_demographics(session, golden_ids)
finally:
    session.close()

if cluster_rows.empty:
    st.warning(
        "No job_skill_clusters rows found for the golden jobs -- "
        "run `docker compose run --rm cluster-nightly` first."
    )
    st.stop()

cluster_rows["job_cluster_name"] = cluster_rows["cluster_label"].map(
    name_clusters_by_majority(cluster_rows, "cluster_label", "domain")
)
profile_rows = cluster_rows.merge(demographics, on="job_id", how="left")

job_options = {
    f"{row.job_id} -- {row.title} @ {row.company_name}": row.job_id
    for row in profile_rows.sort_values("job_id").itertuples()
}
choice_label = st.selectbox("Select a job", list(job_options.keys()))
job_id = job_options[choice_label]
profile = profile_rows.loc[profile_rows["job_id"] == job_id].iloc[0]

_CLUSTER_PALETTE = px.colors.qualitative.Plotly
_cluster_index = sorted(profile_rows["cluster_label"].unique()).index(profile["cluster_label"])
_avatar_color = _CLUSTER_PALETTE[_cluster_index % len(_CLUSTER_PALETTE)]
_initials = "".join(part[0].upper() for part in profile["company_name"].split()[:2]) or "?"

header_left, header_right = st.columns([1, 5])
with header_left:
    st.markdown(
        f"""<div style="width:90px;height:90px;border-radius:50%;background:{_avatar_color};
        display:flex;align-items:center;justify-content:center;color:white;font-size:32px;
        font-weight:bold;">{_initials}</div>""",
        unsafe_allow_html=True,
    )
with header_right:
    st.markdown(f"### {profile['title']}")
    st.caption(f"Job #{job_id} -- {profile['company_name']}")
    info_cols = st.columns(4)
    info_cols[0].metric("Location", f"{profile['location_city']}, {profile['location_region']}")
    salary = (
        f"{profile['salary_min']:,.0f} - {profile['salary_max']:,.0f}"
        if pd.notna(profile["salary_min"]) and pd.notna(profile["salary_max"]) else "unknown"
    )
    info_cols[1].metric("Salary range", salary)
    info_cols[2].metric("Domain", profile["domain"])
    info_cols[3].metric("Occupation", profile["occupation"])
    info_cols = st.columns(2)
    info_cols[0].metric("Job cluster", profile["job_cluster_name"])
    info_cols[1].metric("Louvain community", profile["louvain_community_id"])

st.markdown("#### Required skills")
session = SessionLocal()
try:
    skills_detail = load_job_skill_detail(session, job_id)
finally:
    session.close()
st.dataframe(skills_detail, use_container_width=True, hide_index=True)

st.markdown("#### Matching candidates")
st.caption(
    "The real matching engine (app/matching/rank.py), unmodified, called the same way the "
    "production API does, in its reverse direction (rank_candidates_for_job) -- against the "
    "full candidate pool, not just golden candidates."
)
session = SessionLocal()
try:
    matches = rank_candidates_for_job(session, job_id, top_k=10)
    skill_names = session.execute(select(Skill.id, Skill.name)).all()
finally:
    session.close()
skill_name_by_id = dict(skill_names)

if not matches:
    st.info("No eligible matching candidates found for this job.")
else:
    match_rows = []
    for m in matches:
        missing_required = m.skill_gap.confirmed_absent_required | m.skill_gap.not_recorded_required
        match_rows.append({
            "candidate_id": m.entity_id,
            "name": m.label,
            "total_score": round(m.score.total, 3),
            "skill_score": round(m.score.skill, 3),
            "education_score": round(m.score.education, 3),
            "missing_required_skills": ", ".join(
                sorted(skill_name_by_id.get(i, f"#{i}") for i in missing_required)
            ) or "(none)",
        })
    st.dataframe(pd.DataFrame(match_rows), use_container_width=True, hide_index=True)

st.markdown("#### Similar jobs")
st.caption(
    "Nearest neighbors by required-skill-embedding distance (same new capability as page 5's "
    "similar-candidates section, mirrored for jobs), with same-cluster/same-Louvain-community "
    "flags alongside as a cross-check against the graph-based notion of similarity."
)
similar = find_similar_entities(cluster_rows, "job_id", job_id, top_n=10)
similar = similar.merge(demographics, on="job_id", how="left").merge(
    cluster_rows[["job_id", "domain", "occupation"]], on="job_id", how="left"
)
st.dataframe(
    similar[[
        "job_id", "title", "company_name", "domain", "occupation",
        "distance", "same_skill_cluster", "same_louvain_community",
    ]],
    use_container_width=True, hide_index=True,
)

st.markdown("#### Skill gap")
st.caption(
    "Two independent populations: what similar jobs require that this one doesn't (same "
    "popularity-ranking method as page 5), and -- the reverse direction -- which required "
    "skills even this job's own top matching candidates commonly lack, straight from the "
    "matching engine's own skill-gap analysis, not a second implementation."
)
target_skill_names = set(skills_detail["skill_name"])
gap_left, gap_right = st.columns(2)
with gap_left:
    st.markdown("##### vs. similar jobs")
    session = SessionLocal()
    try:
        peer_skill_rows = load_job_skill_names(session, similar["job_id"].tolist())
    finally:
        session.close()
    peer_gap = compute_peer_skill_gap(target_skill_names, peer_skill_rows, peer_count=len(similar))
    if peer_gap.empty:
        st.info("This job already requires every skill similar jobs require.")
    else:
        render_top_skills_bar_chart(
            peer_gap, "Skills similar jobs require that this one doesn't", height=350
        )
with gap_right:
    st.markdown("##### vs. top matching candidates")
    if not matches:
        st.info("No matching candidates to compare against.")
    else:
        unmet = compute_unmet_requirement_frequency(skill_name_by_id, matches)
        if unmet.empty:
            st.info("No required skill is missing across this job's top matching candidates.")
        else:
            render_top_skills_bar_chart(
                unmet, "Required skills even top candidates commonly lack", height=350
            )

st.markdown("#### Engagement trend vs. skill cluster")
cluster_job_ids = cluster_rows.loc[
    cluster_rows["cluster_label"] == profile["cluster_label"], "job_id"
].tolist()
st.caption(
    f"Real candidate views/clicks/applications/saves on this job over the last 4 "
    f"non-overlapping 7-day periods (oldest to newest), against the average for this job's "
    f"skill cluster ({profile['job_cluster_name']}, {len(cluster_job_ids)} jobs) -- a straight "
    f"aggregation of real `analytics.behavioral_events` rows. A small cluster's average is "
    f"noisier than a large one's -- the job count above is the context for reading it."
)
session = SessionLocal()
try:
    trend_result = compute_job_engagement_trend_vs_cluster(session, job_id, cluster_job_ids)
finally:
    session.close()
trend_result["week_label"] = trend_result["week_bucket"].map(WEEK_BUCKET_LABELS)
trend_long = trend_result.melt(
    id_vars=["week_bucket", "week_label"], value_vars=["job_count", "cluster_avg"],
    var_name="series", value_name="count",
)
trend_long["series"] = trend_long["series"].map({"job_count": "This job", "cluster_avg": "Cluster average"})
trend_fig = px.line(
    trend_long, x="week_label", y="count", color="series", markers=True,
    category_orders={"week_label": [WEEK_BUCKET_LABELS[b] for b in WEEK_BUCKET_ORDER]},
)
trend_fig.update_layout(height=400, legend=dict(orientation="h", y=-0.3))
st.plotly_chart(trend_fig, use_container_width=True)
