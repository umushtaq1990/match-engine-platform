"""Behavioral Analytics: the persona each golden candidate was deliberately
assigned (app/golden/generate_persona_events.py) against the *actual*
rule-based segment that behavior produces (app/analytics/segmentation.py,
unchanged, real production logic) -- "what we intended" vs. "what the
real segmentation engine says," for the same 1000 candidates this whole
golden-dataset v2 rework built. Cross-tabbed with the same domain/
occupation taxonomy the other pages use, so engagement patterns can be
read alongside skill-extraction quality, not as a separate dataset.

Completed to the same depth as the skill-clustering pages: an elbow/
silhouette diagnostic for the k=5 behavioral fit, a t-SNE projection of
the same KPI vectors with a color-by toggle, population distribution,
composition bar charts in place of plain crosstab tables, and a weekly
behavioral-event trend per cluster against the overall population
average -- all reusing the exact same helpers those pages already built,
not new ones. See docs/architecture/data-platform/skill-clustering.md.
"""
import pandas as pd
import plotly.express as px
import streamlit as st

from app.db import SessionLocal
from app.golden.drilldown.chart import (
    cascading_filters,
    render_composition_bar_chart,
    render_k_diagnostic_chart,
    render_population_bar_chart,
)
from app.golden.drilldown.data import (
    DEFAULT_CLUSTER_COUNT,
    SEGMENT_THRESHOLDS_CAPTION,
    TREND_EVENT_LABELS,
    WEEK_BUCKET_LABELS,
    WEEK_BUCKET_ORDER,
    compute_behavioral_clusters,
    compute_behavioral_feature_vectors,
    compute_behavioral_k_diagnostics,
    compute_cluster_weekly_trend_vs_population,
    compute_tsne_projection,
    load_behavioral_rows,
    load_weekly_event_trend,
    name_clusters_by_majority,
)

st.set_page_config(page_title="Behavioral Analytics", layout="wide")
st.title("Behavioral Analytics -- persona vs. actual segment")
st.caption(SEGMENT_THRESHOLDS_CAPTION)

session = SessionLocal()
try:
    rows = load_behavioral_rows(session)
finally:
    session.close()

if rows.empty:
    st.warning("No golden candidates found -- run golden-assign-candidates first.")
    st.stop()

rows["cluster"] = compute_behavioral_clusters(rows)
# Named by majority persona -- raw cluster_0..4 labels mean nothing to a
# reader, same name_clusters_by_majority helper the skill-clustering
# pages use, just on persona instead of domain.
rows["cluster_name"] = rows["cluster"].map(name_clusters_by_majority(rows, "cluster", "persona"))

col1, col2, col3, col4 = st.columns(4)
col1.metric("Golden candidates", len(rows))
col2.metric("Dormant", int((rows["segment"] == "dormant").sum()))
col3.metric("Active seekers", int((rows["segment"] == "active_seeker").sum()))
col4.metric("Behavioral clusters", rows["cluster"].nunique())

st.markdown("#### How many clusters? Elbow + silhouette diagnostic")
st.caption(
    "The actual justification for k=5, not an assumption: inertia (elbow, left axis) and "
    "silhouette score (right axis) across a range of k, refit here over the same behavioral KPI "
    "vectors the real fit uses. k=5 is chosen to match the 5 personas directly (so a cluster "
    "lining up tightly with one persona is a real, comparable signal), not picked by this sweep "
    "alone -- if the best-supported k drifts from 5, that's worth a second look, shown here "
    "rather than hidden."
)
current_k = DEFAULT_CLUSTER_COUNT
behavioral_vectors = compute_behavioral_feature_vectors(rows)
diagnostics = compute_behavioral_k_diagnostics(rows)
render_k_diagnostic_chart(
    diagnostics, current_k, f"Behavioral clusters (k-means over KPIs, current k={current_k})"
)

st.markdown("#### t-SNE projection of behavioral KPIs")
st.caption(
    "Visualization only -- t-SNE can't place a new point, so it's recomputed fresh from whatever's "
    "shown here and never used to assign a candidate's cluster (that's compute_behavioral_clusters' "
    "own k-means fit). The *positions* are always the same KPI-vector projection -- only the "
    "*coloring* switches."
)
color_choice = st.radio("Color points by", ["Cluster", "Persona", "Segment", "Domain"], horizontal=True)
color_column = {
    "Cluster": "cluster_name", "Persona": "persona", "Segment": "segment", "Domain": "domain",
}[color_choice]
x, y = compute_tsne_projection(behavioral_vectors)
plot_df = rows.assign(tsne_x=x, tsne_y=y)
fig = px.scatter(
    plot_df, x="tsne_x", y="tsne_y", color=color_column,
    hover_data=["candidate_id", "domain", "occupation", "cluster_name", "persona", "segment"],
)
fig.update_layout(height=550)
st.plotly_chart(fig, use_container_width=True)

st.markdown(f"#### Population distribution by {color_choice.lower()}")
render_population_bar_chart(rows, color_column, f"Candidates per {color_choice.lower()}")

left, right = st.columns(2)
with left:
    st.markdown("#### Persona (intended)")
    persona_counts = rows["persona"].value_counts().reset_index()
    persona_counts.columns = ["persona", "count"]
    st.plotly_chart(px.bar(persona_counts, x="persona", y="count"), use_container_width=True)
with right:
    st.markdown("#### Segment (actual, rule-based)")
    segment_counts = rows["segment"].value_counts().reset_index()
    segment_counts.columns = ["segment", "count"]
    st.plotly_chart(px.bar(segment_counts, x="segment", y="count"), use_container_width=True)

st.markdown("#### Persona vs. segment -- does intended engagement match the real segmentation logic?")
persona_segment = pd.crosstab(rows["persona"], rows["segment"])
render_composition_bar_chart(persona_segment, "Segment composition per persona")
with st.expander("Show exact table"):
    st.dataframe(persona_segment, use_container_width=True)

st.markdown("#### Segment by profession domain")
domain_segment = pd.crosstab(rows["domain"], rows["segment"])
render_composition_bar_chart(domain_segment, "Segment composition per domain")
with st.expander("Show exact table"):
    st.dataframe(domain_segment, use_container_width=True)

st.markdown(
    "#### Unsupervised check: k-means (k=5) over the same KPIs, vs. the persona we actually assigned"
)
st.caption(
    "segmentation.py's own docstring calls real clustering \"v3, only if the rule-based baseline is "
    "shown insufficient\" -- this is that check, not an assumption either way."
)
cluster_persona = pd.crosstab(rows["cluster_name"], rows["persona"])
render_composition_bar_chart(cluster_persona, "Persona composition per behavioral cluster")
with st.expander("Show exact table"):
    st.dataframe(cluster_persona, use_container_width=True)

st.markdown("#### Weekly behavioral trend per cluster, vs. the overall population average")
cluster_view_options = ["Overall population"] + sorted(rows["cluster_name"].unique())
cluster_view_choice = st.selectbox("Show trend for", cluster_view_options)
all_candidate_ids = rows["candidate_id"].tolist()
session = SessionLocal()
try:
    trend_df = load_weekly_event_trend(session, all_candidate_ids)
finally:
    session.close()

if trend_df.empty:
    st.info("No behavioral events in the last 28 days for the golden population.")
elif cluster_view_choice == "Overall population":
    st.caption("The overall population's own average has nothing to compare against -- pick a cluster above.")
else:
    cluster_candidate_ids = rows.loc[rows["cluster_name"] == cluster_view_choice, "candidate_id"].tolist()
    st.caption(
        f"Real `analytics.behavioral_events` volume for **{cluster_view_choice}** "
        f"({len(cluster_candidate_ids)} candidates) against the full golden population's average "
        f"({len(all_candidate_ids)} candidates), over the last 4 non-overlapping 7-day periods "
        f"(oldest to newest). A small cluster's average is noisier than a large one's -- the "
        f"candidate counts above are the context for reading it."
    )
    trend_result = compute_cluster_weekly_trend_vs_population(
        trend_df, cluster_candidate_ids, all_candidate_ids
    )
    trend_result["week_label"] = trend_result["week_bucket"].map(WEEK_BUCKET_LABELS)
    trend_result["event_label"] = trend_result["event_type"].map(TREND_EVENT_LABELS)
    trend_long = trend_result.melt(
        id_vars=["week_bucket", "week_label", "event_label"],
        value_vars=["cluster_avg", "population_avg"],
        var_name="series", value_name="count",
    )
    trend_long["series"] = trend_long["series"].map({
        "cluster_avg": cluster_view_choice, "population_avg": "Overall population",
    })
    trend_fig = px.line(
        trend_long, x="week_label", y="count", color="series", facet_col="event_label",
        markers=True, category_orders={
            "week_label": [WEEK_BUCKET_LABELS[b] for b in WEEK_BUCKET_ORDER],
            "event_label": list(TREND_EVENT_LABELS.values()),
        },
    )
    trend_fig.update_layout(height=400, legend=dict(orientation="h", y=-0.3))
    trend_fig.update_yaxes(matches=None)
    st.plotly_chart(trend_fig, use_container_width=True)

st.markdown("### Drill into individual candidates")
filtered = cascading_filters(rows, ["domain", "occupation", "persona", "segment", "cluster_name"])
st.caption(f"{len(filtered)} matching candidate(s)")
st.dataframe(
    filtered[[
        "candidate_id", "domain", "occupation", "persona", "segment", "cluster_name",
        "days_since_last_event", "event_count_window", "shown_count", "applied_count", "conversion_rate",
    ]],
    use_container_width=True, hide_index=True,
)
