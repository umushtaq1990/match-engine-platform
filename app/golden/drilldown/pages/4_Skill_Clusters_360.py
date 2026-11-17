"""Skill Clusters 360: the real-time k-means skill cluster each golden
candidate is assigned to (app/clustering/kmeans_skill_clusters.py), a
t-SNE projection of the same skills embeddings for a human-readable
visual (computed fresh here, never persisted or used for assignment --
see app/golden/drilldown/data.py's compute_tsne_projection), the most
recent nightly Louvain community it's been linked to
(app/clustering/nightly_job.py, which is how the two independent
clustering methods -- fast/approximate k-means and slow/explainable
Louvain -- get connected), and the same behavioral segment/persona page 3
already computes -- the actual "360 view": skill identity next to known
taxonomy next to real platform engagement, for the same golden candidates
every other page in this app uses. See
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
)
from app.golden.drilldown.data import (
    DEFAULT_CLUSTER_COUNT,
    compute_behavioral_clusters,
    compute_behavioral_k_diagnostics,
    compute_k_diagnostics,
    compute_tsne_projection,
    load_behavioral_rows,
    load_skill_cluster_rows,
    name_clusters_by_majority,
)

st.set_page_config(page_title="Skill Clusters 360", layout="wide")
st.title("Skill Clusters 360 -- skill identity, taxonomy, and real engagement")

session = SessionLocal()
try:
    rows = load_skill_cluster_rows(session)
    behavioral_rows = load_behavioral_rows(session)
finally:
    session.close()

if rows.empty:
    st.warning(
        "No candidate_skill_clusters rows found for the golden candidates -- "
        "run `docker compose run --rm cluster-nightly` first."
    )
    st.stop()

behavioral_rows["behavioral_cluster"] = compute_behavioral_clusters(behavioral_rows)
merged = rows.merge(
    behavioral_rows[["candidate_id", "segment", "persona", "behavioral_cluster"]],
    on="candidate_id", how="left",
)

# Named by majority vote (most common domain/persona among a cluster's own
# members, raw id kept in parentheses for traceability) -- raw k-means
# labels/Louvain ids/behavioral cluster ids mean nothing to a reader who
# isn't already looking at this code. See data.py's name_clusters_by_majority.
merged["skill_cluster_name"] = merged["cluster_label"].map(
    name_clusters_by_majority(merged, "cluster_label", "domain")
)
merged["louvain_community_name"] = merged["louvain_community_id"].map(
    name_clusters_by_majority(merged, "louvain_community_id", "domain")
)
merged["behavioral_cluster_name"] = merged["behavioral_cluster"].map(
    name_clusters_by_majority(merged, "behavioral_cluster", "persona")
)

col1, col2, col3 = st.columns(3)
linked = rows[rows["louvain_community_id"] != "unknown"]
col1.metric("Golden candidates clustered", len(rows))
col2.metric("k-means skill clusters", rows["cluster_label"].nunique())
col3.metric("Louvain communities linked", linked["louvain_community_id"].nunique())

current_skill_k = rows["cluster_label"].nunique()
current_behavioral_k = DEFAULT_CLUSTER_COUNT
vectors = np.array(merged["vector"].tolist())

st.markdown("#### How many clusters? Elbow + silhouette diagnostic, skill vs. behavioral")
st.caption(
    "The actual justification for k, not an assumption: inertia (elbow -- where adding another "
    "cluster stops helping much, left axis) and silhouette score (how well-separated the "
    "clusters are, right axis) across a range of k, for both the skill-embedding clustering on "
    "this page and the behavioral-KPI clustering page 3 uses -- side by side so the two are "
    "directly comparable, not just individually justified."
)
diag_left, diag_right = st.columns(2)
with diag_left:
    skill_diagnostics = compute_k_diagnostics(vectors)
    render_k_diagnostic_chart(
        skill_diagnostics, current_skill_k,
        f"Skill clusters (k-means over embeddings, current k={current_skill_k})",
    )
with diag_right:
    behavioral_diagnostics = compute_behavioral_k_diagnostics(behavioral_rows)
    render_k_diagnostic_chart(
        behavioral_diagnostics, current_behavioral_k,
        f"Behavioral clusters (k-means over KPIs, current k={current_behavioral_k})",
    )
st.caption(
    "Silhouette scores here are low and fairly flat across k (weak global separation, "
    "consistent with knowledge-graph.md's own candidate/job purity findings) -- look for the "
    "local peak(s), not a single obviously-best k. If the best-supported k doesn't match the "
    "current one, that's a real signal to refit with a different k, not something to explain away."
)

st.markdown("#### t-SNE projection of skills embeddings")
st.caption(
    "Visualization only -- t-SNE has no way to place a new point, so it's recomputed fresh "
    "from whatever's shown here and never used to assign a candidate's cluster (that's the "
    "nearest-centroid lookup in app/clustering/kmeans_skill_clusters.py). The *positions* are "
    "always the skills-embedding projection -- only the *coloring* below switches, so you can "
    "check whether the same geometric regions line up with skill clusters, behavioral clusters, "
    "or neither."
)
color_choice = st.radio(
    "Color points by", ["Skill cluster", "Behavioral cluster", "Domain", "Louvain community"],
    horizontal=True,
)
color_column = {
    "Skill cluster": "skill_cluster_name", "Behavioral cluster": "behavioral_cluster_name",
    "Domain": "domain", "Louvain community": "louvain_community_name",
}[color_choice]
x, y = compute_tsne_projection(vectors)
plot_df = merged.assign(tsne_x=x, tsne_y=y)
fig = px.scatter(
    plot_df, x="tsne_x", y="tsne_y", color=color_column,
    hover_data=[
        "candidate_id", "domain", "occupation", "skill_cluster_name",
        "louvain_community_name", "segment", "persona",
    ],
)
fig.update_layout(height=550)
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns(2)
with left:
    st.markdown("#### Skill cluster vs. domain/occupation")
    st.caption("Does unsupervised skill clustering line up with the known domain taxonomy?")
    domain_crosstab = pd.crosstab(merged["skill_cluster_name"], merged["domain"])
    render_composition_bar_chart(domain_crosstab, "Domain composition per skill cluster")
    with st.expander("Show exact table"):
        st.dataframe(domain_crosstab, use_container_width=True)
with right:
    st.markdown("#### Skill cluster vs. Louvain community")
    st.caption(
        "The explicit link between the two clustering methods -- shown as a real crosstab, "
        "not forced into one mapping, since they're independent methods and won't always agree."
    )
    louvain_crosstab = pd.crosstab(merged["skill_cluster_name"], merged["louvain_community_name"])
    render_count_heatmap(louvain_crosstab, "Skill cluster x Louvain community (candidate counts)")
    with st.expander("Show exact table"):
        st.dataframe(louvain_crosstab, use_container_width=True)

st.markdown("#### Skill cluster vs. platform engagement (segment / persona / behavioral cluster)")
st.caption("The 360 view: skill identity next to how candidates actually behave on the platform.")
engagement_tabs = st.tabs(["Segment", "Persona", "Behavioral cluster"])
with engagement_tabs[0]:
    segment_crosstab = pd.crosstab(merged["skill_cluster_name"], merged["segment"])
    render_composition_bar_chart(segment_crosstab, "Segment composition per skill cluster")
    with st.expander("Show exact table"):
        st.dataframe(segment_crosstab, use_container_width=True)
with engagement_tabs[1]:
    persona_crosstab = pd.crosstab(merged["skill_cluster_name"], merged["persona"])
    render_composition_bar_chart(persona_crosstab, "Persona composition per skill cluster")
    with st.expander("Show exact table"):
        st.dataframe(persona_crosstab, use_container_width=True)
with engagement_tabs[2]:
    behavioral_crosstab = pd.crosstab(merged["skill_cluster_name"], merged["behavioral_cluster_name"])
    render_composition_bar_chart(behavioral_crosstab, "Behavioral cluster composition per skill cluster")
    with st.expander("Show exact table"):
        st.dataframe(behavioral_crosstab, use_container_width=True)

st.markdown("### Drill into individual candidates")
filtered = cascading_filters(
    merged, ["domain", "skill_cluster_name", "louvain_community_name", "segment"]
)
st.caption(f"{len(filtered)} matching candidate(s)")
st.dataframe(
    filtered[[
        "candidate_id", "domain", "occupation", "skill_cluster_name", "louvain_community_name",
        "segment", "persona", "behavioral_cluster_name",
    ]],
    use_container_width=True, hide_index=True,
)
