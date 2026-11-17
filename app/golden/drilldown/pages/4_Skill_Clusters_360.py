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
from app.golden.drilldown.chart import cascading_filters
from app.golden.drilldown.data import (
    compute_behavioral_clusters,
    compute_tsne_projection,
    load_behavioral_rows,
    load_skill_cluster_rows,
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

col1, col2, col3 = st.columns(3)
linked = rows[rows["louvain_community_id"] != "unknown"]
col1.metric("Golden candidates clustered", len(rows))
col2.metric("k-means skill clusters", rows["cluster_label"].nunique())
col3.metric("Louvain communities linked", linked["louvain_community_id"].nunique())

st.markdown("#### t-SNE projection of skills embeddings, colored by k-means skill cluster")
st.caption(
    "Visualization only -- t-SNE has no way to place a new point, so it's recomputed fresh "
    "from whatever's shown here and never used to assign a candidate's cluster (that's the "
    "nearest-centroid lookup in app/clustering/kmeans_skill_clusters.py)."
)
vectors = np.array(merged["vector"].tolist())
x, y = compute_tsne_projection(vectors)
plot_df = merged.assign(tsne_x=x, tsne_y=y)
fig = px.scatter(
    plot_df, x="tsne_x", y="tsne_y", color="cluster_label",
    hover_data=["candidate_id", "domain", "occupation", "louvain_community_id", "segment"],
)
fig.update_layout(height=550)
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns(2)
with left:
    st.markdown("#### Skill cluster vs. domain/occupation")
    st.caption("Does unsupervised skill clustering line up with the known domain taxonomy?")
    st.dataframe(pd.crosstab(merged["cluster_label"], merged["domain"]), use_container_width=True)
with right:
    st.markdown("#### Skill cluster vs. Louvain community")
    st.caption(
        "The explicit link between the two clustering methods -- shown as a real crosstab, "
        "not forced into one mapping, since they're independent methods and won't always agree."
    )
    louvain_crosstab = pd.crosstab(merged["cluster_label"], merged["louvain_community_id"])
    st.dataframe(louvain_crosstab, use_container_width=True)

st.markdown("#### Skill cluster vs. platform engagement (segment / persona / behavioral cluster)")
st.caption("The 360 view: skill identity next to how candidates actually behave on the platform.")
engagement_tabs = st.tabs(["Segment", "Persona", "Behavioral cluster"])
with engagement_tabs[0]:
    st.dataframe(pd.crosstab(merged["cluster_label"], merged["segment"]), use_container_width=True)
with engagement_tabs[1]:
    st.dataframe(pd.crosstab(merged["cluster_label"], merged["persona"]), use_container_width=True)
with engagement_tabs[2]:
    st.dataframe(pd.crosstab(merged["cluster_label"], merged["behavioral_cluster"]), use_container_width=True)

st.markdown("### Drill into individual candidates")
filtered = cascading_filters(merged, ["domain", "cluster_label", "louvain_community_id", "segment"])
st.caption(f"{len(filtered)} matching candidate(s)")
st.dataframe(
    filtered[[
        "candidate_id", "domain", "occupation", "cluster_label", "louvain_community_id",
        "segment", "persona", "behavioral_cluster",
    ]],
    use_container_width=True, hide_index=True,
)
