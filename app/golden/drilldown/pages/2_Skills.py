"""Skills drill-down: entity type -> domain -> occupation -> skill type
(technical/soft), colored by average match confidence. Leaves are
*matched skill mentions*, not whole examples -- a single CV/job can have
both technical and soft skills, so this is a separate hierarchy from the
Candidates & Job Posts page's per-example tree (see app/golden/
build_evaluation_tree.py's module docstring for why).
"""
import streamlit as st

from app.db import SessionLocal
from app.golden.drilldown.chart import cascading_filters, render_drilldown_chart
from app.golden.drilldown.data import load_skill_match_rows, select_run_in_sidebar

st.set_page_config(page_title="Skills", layout="wide")
st.title("Skills -- by match confidence")

session = SessionLocal()
try:
    run_id = select_run_in_sidebar(session)
    if run_id is None:
        st.stop()

    rows = load_skill_match_rows(session, run_id)
finally:
    session.close()

if rows.empty:
    st.warning(f"No matched skills found for run {run_id}.")
    st.stop()

st.caption(
    f"{len(rows)} matched skill mentions in run {run_id} -- "
    "click a box below, or use the dropdowns to drill in."
)

render_drilldown_chart(
    rows, "confidence",
    [("entity_type",), ("entity_type", "domain"), ("entity_type", "domain", "occupation"),
     ("entity_type", "domain", "occupation", "skill_type")],
    chart_key="skills_tree",
    filter_keys=["entity_type", "domain", "occupation", "skill_type"],
)

filtered = cascading_filters(rows, ["entity_type", "domain", "occupation", "skill_type"])

st.markdown(f"### {len(filtered)} matching skill mention(s)")
st.dataframe(
    filtered[[
        "example_id", "entity_type", "domain", "occupation",
        "skill", "skill_type", "confidence", "requirement_level", "source_phrase",
    ]],
    use_container_width=True, hide_index=True,
)
