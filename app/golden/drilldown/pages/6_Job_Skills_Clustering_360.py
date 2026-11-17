"""Job Skills Clustering 360: the job-side mirror of page 4 (Candidate
Skills Clustering 360) -- the real-time k-means skill cluster each
golden job is assigned to (app/clustering/kmeans_skill_clusters.py,
entity_type="job"), a t-SNE projection of the same required-skill
embeddings, and the most recent nightly Louvain community it's been
linked to (app/clustering/nightly_job.py). No behavioral-KPI diagnostic
or engagement crosstab here -- jobs don't log in or apply, there's no
job-side equivalent of a candidate's behavioral events, so that part of
page 4 is dropped, not faked. See
docs/architecture/data-platform/skill-clustering.md.
"""
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from app.db import SessionLocal
from app.golden.drilldown.chart import (
    cascading_filters,
    render_composition_bar_chart,
    render_count_heatmap,
    render_k_diagnostic_chart,
    render_population_bar_chart,
    render_top_skills_bar_chart,
)
from app.golden.drilldown.data import (
    compute_k_diagnostics,
    compute_top_skills,
    compute_tsne_projection,
    generate_skill_wordcloud,
    link_clusters_by_domain,
    load_candidate_skill_names,
    load_job_demographics,
    load_job_skill_cluster_rows,
    load_job_skill_names,
    load_skill_cluster_rows,
    name_clusters_by_majority,
)

st.set_page_config(page_title="Job Skills Clustering 360", layout="wide")
st.title("Job Skills Clustering 360 -- job skill identity and taxonomy")

session = SessionLocal()
try:
    rows = load_job_skill_cluster_rows(session)
finally:
    session.close()

if rows.empty:
    st.warning(
        "No job_skill_clusters rows found for the golden jobs -- "
        "run `docker compose run --rm cluster-nightly` first."
    )
    st.stop()

session = SessionLocal()
try:
    demographics = load_job_demographics(session, rows["job_id"].tolist())
    skill_name_rows = load_job_skill_names(session, rows["job_id"].tolist())
    candidate_cluster_rows = load_skill_cluster_rows(session)
    candidate_skill_name_rows = load_candidate_skill_names(
        session, candidate_cluster_rows["candidate_id"].tolist()
    )
finally:
    session.close()

merged = rows.merge(demographics, on="job_id", how="left")

# Named by majority vote (most common domain among a cluster's own
# members, raw id kept in parentheses for traceability) -- same
# reasoning and same helper as page 4's candidate clusters.
merged["job_cluster_name"] = merged["cluster_label"].map(
    name_clusters_by_majority(merged, "cluster_label", "domain")
)
merged["louvain_community_name"] = merged["louvain_community_id"].map(
    name_clusters_by_majority(merged, "louvain_community_id", "domain")
)

# The job-side mirror of page 4's candidate-to-job link -- each cluster
# is its own independent k-means fit, never the same cluster by index,
# but both land on the same real domain taxonomy. See
# data.py's link_clusters_by_domain.
job_to_candidate_cluster = link_clusters_by_domain(
    rows, "cluster_label", candidate_cluster_rows, "cluster_label"
)
candidate_cluster_rows["candidate_cluster_name"] = candidate_cluster_rows["cluster_label"].map(
    name_clusters_by_majority(candidate_cluster_rows, "cluster_label", "domain")
)

col1, col2, col3 = st.columns(3)
linked = rows[rows["louvain_community_id"] != "unknown"]
col1.metric("Golden jobs clustered", len(rows))
col2.metric("k-means job clusters", rows["cluster_label"].nunique())
col3.metric("Louvain communities linked", linked["louvain_community_id"].nunique())

current_k = rows["cluster_label"].nunique()
vectors = np.array(merged["vector"].tolist())

st.markdown("#### How many clusters? Elbow + silhouette diagnostic")
st.caption(
    "The actual justification for k, not an assumption: inertia (elbow, left axis) and "
    "silhouette score (right axis) across a range of k, refit here over these jobs' real "
    "required-skill embeddings."
)
diagnostics = compute_k_diagnostics(vectors)
render_k_diagnostic_chart(
    diagnostics, current_k, f"Job clusters (k-means over embeddings, current k={current_k})"
)
st.caption(
    "Measured at k=8 (silhouette 0.2422, the sweep's own global optimum over all 4,002 jobs) -- "
    "close to, not forced to match, Louvain's independently-found 7 job communities (modularity "
    "0.6879). If the best-supported k here drifts from the current one, that's a real signal to "
    "refit, not something to explain away."
)

st.markdown("#### t-SNE projection of required-skill embeddings")
st.caption(
    "Visualization only -- t-SNE can't place a new point, so it's recomputed fresh from whatever's "
    "shown here and never used to assign a job's cluster (that's the nearest-centroid lookup in "
    "app/clustering/kmeans_skill_clusters.py). The *positions* are always the skills-embedding "
    "projection -- only the *coloring* switches."
)
color_choice = st.radio("Color points by", ["Job cluster", "Domain", "Louvain community"], horizontal=True)
color_column = {
    "Job cluster": "job_cluster_name", "Domain": "domain", "Louvain community": "louvain_community_name",
}[color_choice]
x, y = compute_tsne_projection(vectors)
plot_df = merged.assign(tsne_x=x, tsne_y=y)
fig = px.scatter(
    plot_df, x="tsne_x", y="tsne_y", color=color_column,
    hover_data=[
        "job_id", "title", "company_name", "domain", "occupation",
        "job_cluster_name", "louvain_community_name",
    ],
)
fig.update_layout(height=550)
st.plotly_chart(fig, use_container_width=True)

st.markdown(f"#### Population distribution by {color_choice.lower()}")
st.caption(
    "Cluster sizes are rarely even -- this is the raw job count behind every crosstab/"
    "composition chart below, for whichever coloring is selected above."
)
render_population_bar_chart(merged, color_column, f"Jobs per {color_choice.lower()}")

st.markdown("#### Trending required skills")
st.caption(
    "Real `job_skills` ground truth (not the embedding that drives clustering) -- top 10 required "
    "skills by the proportion of jobs that need them, overall or within one cluster, plus a word "
    "cloud (sized the same way) for a faster visual read."
)
skill_view_options = ["Overall (all clustered jobs)"] + sorted(merged["job_cluster_name"].unique())
skill_view_choice = st.selectbox("Show trending skills for", skill_view_options)
if skill_view_choice == "Overall (all clustered jobs)":
    scoped_job_ids = set(merged["job_id"])
else:
    scoped_job_ids = set(merged.loc[merged["job_cluster_name"] == skill_view_choice, "job_id"])
scoped_skill_rows = skill_name_rows[skill_name_rows["job_id"].isin(scoped_job_ids)]
top_skills = compute_top_skills(scoped_skill_rows, population_size=len(scoped_job_ids))

skills_chart_height = 500
skills_left, skills_right = st.columns([3, 2])
with skills_left:
    render_top_skills_bar_chart(
        top_skills, f"Top 10 required skills -- {skill_view_choice}", height=skills_chart_height
    )
with skills_right:
    if top_skills.empty:
        st.info("No skill data for this selection.")
    else:
        wc_width = 450
        wc_image = generate_skill_wordcloud(
            scoped_skill_rows["skill_name"].value_counts(), width=wc_width, height=skills_chart_height,
        )
        st.image(wc_image, caption=f"Word cloud -- {skill_view_choice}", width=wc_width)

st.markdown("#### Candidate pool link: who's available for this job segment")
if skill_view_choice == "Overall (all clustered jobs)":
    linked_candidate_ids = set(candidate_cluster_rows["candidate_id"])
    linked_label = "all clustered candidates (no single job cluster selected above)"
else:
    raw_cluster_label = merged.loc[
        merged["job_cluster_name"] == skill_view_choice, "cluster_label"
    ].iloc[0]
    linked_candidate_clusters = job_to_candidate_cluster.get(raw_cluster_label, [])
    in_linked_clusters = candidate_cluster_rows["cluster_label"].isin(linked_candidate_clusters)
    linked_names = sorted(candidate_cluster_rows.loc[in_linked_clusters, "candidate_cluster_name"].unique())
    linked_candidate_ids = set(candidate_cluster_rows.loc[in_linked_clusters, "candidate_id"])
    linked_label = ", ".join(linked_names) if linked_names else "(no candidate cluster shares this domain)"
st.caption(
    f"Linked candidate cluster(s) by shared majority domain: **{linked_label}**. Popularity-ranked "
    "skills across those candidates (same compute_top_skills method as the trending-skills chart "
    "above, just on the linked candidate population) -- what the available talent pool for this "
    "job segment already has, not an assumption."
)
linked_candidate_skill_rows = candidate_skill_name_rows[
    candidate_skill_name_rows["candidate_id"].isin(linked_candidate_ids)
]
pool_skills = compute_top_skills(linked_candidate_skill_rows, population_size=len(linked_candidate_ids))
if pool_skills.empty:
    st.info("No linked candidate data for this selection.")
else:
    render_top_skills_bar_chart(
        pool_skills, f"Top 10 skills in the talent pool -- {linked_label}", height=450
    )

st.markdown("#### Job cluster vs. domain/occupation")
st.caption("Does unsupervised job clustering line up with the known domain taxonomy?")
domain_crosstab = pd.crosstab(merged["job_cluster_name"], merged["domain"])
render_composition_bar_chart(domain_crosstab, "Domain composition per job cluster", height=650)
with st.expander("Show exact table"):
    st.dataframe(domain_crosstab, use_container_width=True)

st.markdown("#### Job cluster vs. Louvain community")
st.caption(
    "The explicit link between the two clustering methods -- shown as a real crosstab, not "
    "forced into one mapping, since they're independent methods and won't always agree."
)
louvain_crosstab = pd.crosstab(merged["job_cluster_name"], merged["louvain_community_name"])
render_count_heatmap(louvain_crosstab, "Job cluster x Louvain community (job counts)", height=650)
with st.expander("Show exact table"):
    st.dataframe(louvain_crosstab, use_container_width=True)

st.markdown("### Drill into individual jobs")
filtered = cascading_filters(merged, ["domain", "job_cluster_name", "louvain_community_name"])
st.caption(f"{len(filtered)} matching job(s)")
st.dataframe(
    filtered[[
        "job_id", "title", "company_name", "domain", "occupation",
        "job_cluster_name", "louvain_community_name",
    ]],
    use_container_width=True, hide_index=True,
)
