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
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from app.golden.build_evaluation_tree import _icicle_trace


def render_drilldown_chart(rows: pd.DataFrame, value_key: str, levels: list[tuple[str, ...]]) -> None:
    """Renders the icicle chart -- display only, see module docstring for
    why this doesn't also drive the filters below it."""
    row_dicts = rows.to_dict("records")
    trace = _icicle_trace(row_dicts, value_key, "All", levels)
    fig = go.Figure(trace)
    fig.update_layout(margin={"t": 10, "l": 10, "r": 10, "b": 10}, height=500)
    st.plotly_chart(fig, use_container_width=True)


def render_k_diagnostic_chart(diagnostics: pd.DataFrame, current_k: int, title: str) -> None:
    """Elbow (inertia) + silhouette score on one figure, two y-axes --
    inertia on the left (its own scale, thousands), silhouette on the
    right (always 0-1) -- so both curves' shapes are readable together
    without one swamping the other's axis. Used identically for the
    skill-embedding sweep and the behavioral-KPI sweep (see
    app/golden/drilldown/data.py's compute_k_diagnostics/
    compute_behavioral_k_diagnostics) so the two are visually comparable
    side by side."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(
            x=diagnostics["k"], y=diagnostics["inertia"], name="Inertia (elbow)", mode="lines+markers",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=diagnostics["k"], y=diagnostics["silhouette"], name="Silhouette score", mode="lines+markers",
        ),
        secondary_y=True,
    )
    fig.add_vline(x=current_k, line_dash="dash", annotation_text=f"current k={current_k}")
    fig.update_layout(title=title, height=380, legend=dict(orientation="h", y=-0.2))
    fig.update_xaxes(title_text="k")
    fig.update_yaxes(title_text="Inertia", secondary_y=False)
    fig.update_yaxes(title_text="Silhouette score", secondary_y=True)
    st.plotly_chart(fig, use_container_width=True)


def render_composition_bar_chart(crosstab: pd.DataFrame, title: str, height: int = 420) -> None:
    """One 100%-stacked bar per crosstab row, segment widths = proportion
    within that row, but each segment is *labeled* with the real count --
    composition (does this cluster skew toward one category?) is the
    visual story, the exact number stays on the chart rather than only
    living in a separate table. Stacked (not grouped) deliberately: the
    point is comparing a category's share *within* each cluster, which a
    grouped bar doesn't show as directly."""
    pct = crosstab.div(crosstab.sum(axis=1), axis=0) * 100
    fig = go.Figure()
    for col in crosstab.columns:
        fig.add_trace(go.Bar(
            x=crosstab.index.astype(str), y=pct[col], name=str(col),
            text=crosstab[col], texttemplate="%{text}", textposition="inside",
        ))
    fig.update_layout(
        barmode="stack", title=title, height=height, yaxis_title="% of row (count labeled)",
        legend=dict(orientation="h", y=-0.3),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_count_heatmap(crosstab: pd.DataFrame, title: str, height: int = 420) -> None:
    """Cell color = count, cell text = the same count -- for a crosstab
    too wide for a stacked bar's color-per-segment to stay legible (e.g.
    cluster x cluster), a heatmap shows concentration at a glance while
    keeping every exact number visible, not hidden behind a hover."""
    fig = px.imshow(
        crosstab, text_auto=True, aspect="auto", title=title,
        labels={"x": crosstab.columns.name or "", "y": crosstab.index.name or "", "color": "count"},
    )
    fig.update_layout(height=height)
    st.plotly_chart(fig, use_container_width=True)


def render_top_skills_bar_chart(top_skills: pd.DataFrame, title: str, height: int = 420) -> None:
    """Horizontal bar, one per top skill, sorted so the most common ends
    up on top -- the bar's length is the real proportion of the
    population that has it, labeled with both proportion and the
    underlying count (a count alone reads differently for a cluster of
    150 vs. 500)."""
    ordered = top_skills.sort_values("proportion")
    fig = go.Figure(go.Bar(
        x=ordered["proportion"] * 100, y=ordered["skill_name"], orientation="h",
        text=[f"{p:.0%} (n={c})" for p, c in zip(ordered["proportion"], ordered["count"])],
        textposition="outside",
    ))
    fig.update_layout(title=title, height=height, xaxis_title="% of population with this skill")
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
