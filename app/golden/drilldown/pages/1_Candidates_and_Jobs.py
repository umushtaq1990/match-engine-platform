"""Candidates & Job Posts drill-down: entity type -> domain -> occupation,
colored by F1, click (or use the dropdowns below the chart) to narrow down
to the full per-example table -- actual/expected skills, matched/
unmatched, precision/recall/F1, for every record in that leaf.
"""
import streamlit as st

from app.db import SessionLocal
from app.golden.drilldown.chart import cascading_filters, render_drilldown_chart
from app.golden.drilldown.data import load_example_rows, select_run_in_sidebar

st.set_page_config(page_title="Candidates & Job Posts", layout="wide")
st.title("Candidates & Job Posts -- by F1")

session = SessionLocal()
try:
    run_id = select_run_in_sidebar(session)
    if run_id is None:
        st.stop()

    rows = load_example_rows(session, run_id)
finally:
    session.close()

if rows.empty:
    st.warning(f"No golden_cv/golden_job examples found for run {run_id}.")
    st.stop()

st.caption(f"{len(rows)} examples in run {run_id} -- click a box below, or use the dropdowns to drill in.")

render_drilldown_chart(
    rows, "f1",
    [("entity_type",), ("entity_type", "domain"), ("entity_type", "domain", "occupation")],
    chart_key="candidates_jobs_tree",
    filter_keys=["entity_type", "domain", "occupation"],
)

filtered = cascading_filters(rows, ["entity_type", "domain", "occupation"])

st.markdown(f"### {len(filtered)} matching record(s)")
st.dataframe(
    filtered[[
        "example_id", "title", "entity_type", "domain", "occupation",
        "precision", "recall", "f1", "expected_skills", "matched_skills", "unmatched_phrases",
    ]],
    use_container_width=True, hide_index=True,
)

if len(filtered) == 1:
    row = filtered.iloc[0]
    if row["text_preview"]:
        st.markdown("#### Job posting text (first 500 chars)")
        st.text(row["text_preview"])
