"""Shared icicle-chart + click-to-filter helpers for both drill-down pages.
Reuses app/golden/build_evaluation_tree.py's _aggregate/_icicle_trace
directly (same hierarchy-building logic as the static report) rather than
duplicating it -- this app adds live run-switching and click-to-filter on
top of the same core chart, it doesn't reimplement the chart itself.
"""
import pandas as pd
import streamlit as st

from app.golden.build_evaluation_tree import _icicle_trace


def render_drilldown_chart(
    rows: pd.DataFrame, value_key: str, levels: list[tuple[str, ...]], chart_key: str,
    filter_keys: list[str],
) -> None:
    """Renders the icicle chart and, on a node click, writes the clicked
    node's own group-key values into st.session_state under `filter_keys`
    (e.g. ["entity_type", "domain", "occupation"]) *before* any selectbox
    bound to those same keys is instantiated -- the one reliable way to
    make a chart click and a dropdown stay in sync in Streamlit, since a
    widget's value can only be set via session_state prior to its own
    render call in the same run.
    """
    row_dicts = rows.to_dict("records")
    trace = _icicle_trace(row_dicts, value_key, "All", levels)
    import plotly.graph_objects as go
    fig = go.Figure(trace)
    fig.update_layout(margin={"t": 10, "l": 10, "r": 10, "b": 10}, height=500)

    event = st.plotly_chart(fig, on_select="rerun", key=chart_key, use_container_width=True)

    points = (event or {}).get("selection", {}).get("points", [])
    if points:
        point = points[0]
        # Streamlit's on_select payload has no id/label/parent field at all
        # (confirmed against its own documented schema: curve_number,
        # point_number, point_index, x, y, customdata, marker_size,
        # legendgroup) -- customdata (set to the trace's own `ids` list in
        # _icicle_trace) is the reliable path; point_index indexing
        # straight into the same `ids` list this trace was built from is
        # the fallback, in case customdata isn't forwarded for this chart
        # type either.
        clicked_id = None
        customdata = point.get("customdata")
        if customdata:
            clicked_id = customdata[0] if isinstance(customdata, list) else customdata
        if not clicked_id:
            point_index = point.get("point_index", point.get("point_number"))
            if point_index is not None and point_index < len(trace.ids):
                clicked_id = trace.ids[point_index]

        if clicked_id:
            # id format: "root|entity_type:Candidates|domain:technology|occupation:X"
            segments = clicked_id.split("|")[1:]
            parsed = dict(seg.split(":", 1) for seg in segments if ":" in seg)
            for key in filter_keys:
                if key in parsed:
                    st.session_state[f"filter_{key}"] = parsed[key]
        else:
            # Neither extraction path found an id -- surface the raw event
            # so this is diagnosable without needing to add print
            # statements and redeploy; remove once click-to-drill is
            # confirmed working end to end in a real browser.
            with st.expander("Debug: chart click produced no usable id (click to inspect)"):
                st.write(point)


def cascading_filters(rows: pd.DataFrame, filter_keys: list[str]) -> pd.DataFrame:
    """Renders one selectbox per key in `filter_keys`, each narrowed to
    values still possible given the previous ones (and pre-filled from a
    chart click via the same session_state keys render_drilldown_chart
    writes to). Returns the fully filtered DataFrame."""
    filtered = rows
    cols = st.columns(len(filter_keys))
    for col, key in zip(cols, filter_keys):
        state_key = f"filter_{key}"
        options = ["(all)"] + sorted(filtered[key].dropna().unique().tolist())
        current = st.session_state.get(state_key, "(all)")
        if current not in options:
            # A narrower upstream filter (or a stale chart-click value) can
            # make the previous choice impossible here -- reset in
            # session_state itself before the widget reads it, since a
            # selectbox raises if its bound key holds a value outside its
            # own options list.
            st.session_state[state_key] = "(all)"
        with col:
            choice = st.selectbox(key.replace("_", " ").title(), options, key=state_key)
        if choice != "(all)":
            filtered = filtered[filtered[key] == choice]
    return filtered
