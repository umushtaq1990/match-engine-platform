"""Shared icicle-chart + drill-down-filter helpers for both drill-down
pages. Reuses app/golden/build_evaluation_tree.py's _aggregate/
_icicle_trace directly (same hierarchy-building logic as the static
report) rather than duplicating it.

Click-to-drill on the chart itself was tried and confirmed NOT to work:
Streamlit's `on_select="rerun"` is built around Plotly's generic
"selectedpoints" mechanism (the one scatter/bar/etc. use), but Icicle/
Sunburst/Treemap charts fire their own separate click event family in
Plotly.js (e.g. `plotly_sunburstclick`) that Streamlit does not bridge to
Python. Confirmed live, twice: a click on the chart does trigger a
Streamlit rerun (on_select fires), but `event["selection"]["points"]`
comes back an empty list every time, with or without
`fig.update_layout(clickmode="event+select")` (the one documented lever
for this). Not a config problem -- a real gap in what Streamlit currently
supports for this chart family. The cascading dropdowns below are
therefore the one real navigation mechanism, not a fallback for a
flakier primary one.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.golden.build_evaluation_tree import _icicle_trace


def render_drilldown_chart(rows: pd.DataFrame, value_key: str, levels: list[tuple[str, ...]]) -> None:
    """Renders the icicle chart -- display only, see module docstring for
    why this doesn't also drive the filters below it."""
    row_dicts = rows.to_dict("records")
    trace = _icicle_trace(row_dicts, value_key, "All", levels)
    fig = go.Figure(trace)
    fig.update_layout(margin={"t": 10, "l": 10, "r": 10, "b": 10}, height=500)
    st.plotly_chart(fig, use_container_width=True)


def cascading_filters(rows: pd.DataFrame, filter_keys: list[str]) -> pd.DataFrame:
    """Renders one selectbox per key in `filter_keys`, each narrowed to
    values still possible given the previous ones. Returns the fully
    filtered DataFrame -- the actual drill-down mechanism (see module
    docstring for why the chart above doesn't also drive this)."""
    filtered = rows
    cols = st.columns(len(filter_keys))
    for col, key in zip(cols, filter_keys):
        state_key = f"filter_{key}"
        options = ["(all)"] + sorted(filtered[key].dropna().unique().tolist())
        current = st.session_state.get(state_key, "(all)")
        if current not in options:
            # A narrower upstream filter can make the previous choice
            # impossible here -- reset in session_state itself before the
            # widget reads it, since a selectbox raises if its bound key
            # holds a value outside its own options list.
            st.session_state[state_key] = "(all)"
        with col:
            choice = st.selectbox(key.replace("_", " ").title(), options, key=state_key)
        if choice != "(all)":
            filtered = filtered[filtered[key] == choice]
    return filtered
