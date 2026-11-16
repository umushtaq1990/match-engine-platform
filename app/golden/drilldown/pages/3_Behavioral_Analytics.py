"""Behavioral Analytics: the persona each golden candidate was deliberately
assigned (app/golden/generate_persona_events.py) against the *actual*
rule-based segment that behavior produces (app/analytics/segmentation.py,
unchanged, real production logic) -- "what we intended" vs. "what the
real segmentation engine says," for the same 1000 candidates this whole
golden-dataset v2 rework built. Cross-tabbed with the same domain/
occupation taxonomy the other two pages use, so engagement patterns can
be read alongside skill-extraction quality, not as a separate dataset.
"""
import pandas as pd
import plotly.express as px
import streamlit as st

from app.db import SessionLocal
from app.golden.drilldown.chart import cascading_filters
from app.golden.drilldown.data import (
    SEGMENT_THRESHOLDS_CAPTION,
    compute_behavioral_clusters,
    load_behavioral_rows,
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

col1, col2, col3 = st.columns(3)
col1.metric("Golden candidates", len(rows))
col2.metric("Dormant", int((rows["segment"] == "dormant").sum()))
col3.metric("Active seekers", int((rows["segment"] == "active_seeker").sum()))

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
crosstab = pd.crosstab(rows["persona"], rows["segment"])
st.dataframe(crosstab, use_container_width=True)

st.markdown("#### Segment by profession domain")
domain_segment = pd.crosstab(rows["domain"], rows["segment"])
st.dataframe(domain_segment, use_container_width=True)

st.markdown(
    "#### Unsupervised check: k-means (k=5) over the same KPIs, vs. the persona we actually assigned"
)
st.caption(
    "segmentation.py's own docstring calls real clustering \"v3, only if the rule-based baseline is "
    "shown insufficient\" -- this is that check, not an assumption either way. k=5 to match the "
    "5 personas directly, so a cluster lining up tightly with one persona is a real, comparable "
    "signal, not a coincidence of a different k."
)
cluster_persona = pd.crosstab(rows["cluster"], rows["persona"])
st.dataframe(cluster_persona, use_container_width=True)

st.markdown("### Drill into individual candidates")
filtered = cascading_filters(rows, ["domain", "occupation", "persona", "segment", "cluster"])
st.caption(f"{len(filtered)} matching candidate(s)")
st.dataframe(
    filtered[[
        "candidate_id", "domain", "occupation", "persona", "segment", "cluster",
        "days_since_last_event", "event_count_window", "shown_count", "applied_count", "conversion_rate",
    ]],
    use_container_width=True, hide_index=True,
)
