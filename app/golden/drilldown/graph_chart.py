"""Renders an ego-network (graph_explainability.py's node/edge output) as
an interactive Plotly diagram. Deliberately NOT a physics-based
(force-directed) layout -- a deterministic radial one instead: the center
entity at the origin, its skills on an inner ring, and its peers/jobs/
courses on an outer ring, grouped into separate angular sectors by type.
This guarantees no overlap (every node gets its own angle, nothing
competes for space the way a spring layout's nodes can) and reads as
visibly organized rather than a physics simulation's incidental shape.

Node color encodes type; node size and edge width encode connection
strength (shared-skill count, blended with embedding similarity when
available); the center entity gets its own distinct color/symbol so it's
never mistaken for one of its own neighbors. Candidate nodes additionally
get a colored outline for their behavioral segment (dormant/active_seeker/
passive_seeker, app.analytics.segmentation) when that data is supplied --
a second, independent signal layered on without competing with the type
color for the same visual channel. Display only, same convention as this
package's existing chart.py.
"""
import math

import plotly.graph_objects as go
import streamlit as st

_TYPE_COLOR = {
    "Candidate": "#4C78A8", "Job": "#F58518", "Skill": "#72B7B2", "Course": "#54A24B",
}
_CENTER_COLOR = "#E63946"
_SEGMENT_BORDER_COLOR = {
    "active_seeker": "#2CA02C", "passive_seeker": "#FFB703", "dormant": "#6C757D",
}
_DEFAULT_BORDER_COLOR = "rgba(0,0,0,0)"

_CENTER_SIZE = 40
_SKILL_SIZE = 16
_NEIGHBOR_BASE_SIZE = 12
_NEIGHBOR_SIZE_RANGE = 22
_EDGE_BASE_WIDTH = 1.0
_EDGE_WIDTH_RANGE = 4.0
_SKILL_RADIUS = 1.0
_NEIGHBOR_RADIUS = 2.3
_SECTOR_GAP_RADIANS = 0.25
_NEIGHBOR_TYPE_ORDER = ("Candidate", "Job", "Course")


def _node_hover_text(node: dict) -> str:
    if node["is_center"]:
        return f"{node['label']} (this {node['type'].lower()})"
    if node["type"] == "Skill":
        return f"Skill: {node['label']}"
    lines = [node["label"], f"Shared skills: {node['shared_skill_count']}"]
    if node["embedding_similarity"] is not None:
        lines.append(f"Embedding similarity: {node['embedding_similarity']:.3f}")
    if node.get("segment"):
        lines.append(f"Behavioral segment: {node['segment'].replace('_', ' ')}")
    return "<br>".join(lines)


def _node_size(node: dict, max_shared: int) -> float:
    if node["is_center"]:
        return _CENTER_SIZE
    if node["type"] == "Skill":
        return _SKILL_SIZE
    shared = node["shared_skill_count"] or 0
    strength = shared / max_shared if max_shared else 0.0
    sim = node["embedding_similarity"]
    if sim is not None:
        strength = min(1.0, 0.6 * strength + 0.4 * sim)
    return _NEIGHBOR_BASE_SIZE + _NEIGHBOR_SIZE_RANGE * strength


def _radial_positions(nodes: list[dict]) -> dict[str, tuple[float, float]]:
    """Deterministic layout: center at the origin, skills evenly spaced on
    an inner ring, neighbors (peers/jobs/courses) on an outer ring split
    into one angular sector per type (in `_NEIGHBOR_TYPE_ORDER`), each
    sector sized proportionally to how many nodes it holds, with a fixed
    gap between sectors so the type grouping is visually obvious, not
    just color-coded.
    """
    positions: dict[str, tuple[float, float]] = {}
    center = next(n for n in nodes if n["is_center"])
    positions[center["node_id"]] = (0.0, 0.0)

    skills = [n for n in nodes if n["type"] == "Skill"]
    for i, node in enumerate(skills):
        angle = 2 * math.pi * i / len(skills)
        positions[node["node_id"]] = (_SKILL_RADIUS * math.cos(angle), _SKILL_RADIUS * math.sin(angle))

    neighbors_by_type = {
        t: [n for n in nodes if not n["is_center"] and n["type"] == t] for t in _NEIGHBOR_TYPE_ORDER
    }
    total_neighbors = sum(len(group) for group in neighbors_by_type.values())
    if total_neighbors == 0:
        return positions

    num_nonempty_groups = sum(1 for group in neighbors_by_type.values() if group)
    available_angle = 2 * math.pi - _SECTOR_GAP_RADIANS * num_nonempty_groups
    cursor = -math.pi / 2  # start the first sector pointing straight down, away from skill labels above
    for node_type in _NEIGHBOR_TYPE_ORDER:
        group = neighbors_by_type[node_type]
        if not group:
            continue
        sector_span = available_angle * (len(group) / total_neighbors)
        for i, node in enumerate(group):
            angle = cursor + sector_span * (i + 0.5) / len(group)
            positions[node["node_id"]] = (
                _NEIGHBOR_RADIUS * math.cos(angle), _NEIGHBOR_RADIUS * math.sin(angle),
            )
        cursor += sector_span + _SECTOR_GAP_RADIANS

    return positions


def render_ego_network(nodes: list[dict], edges: list[dict]) -> None:
    """Renders the ego-network built by graph_explainability.py's
    build_candidate_ego_network/build_job_ego_network.

    Args:
        nodes: each `{node_id, type, label, shared_skill_count,
            embedding_similarity, is_center}`, plus an optional `segment`
            key on Candidate nodes (one of `dormant`/`active_seeker`/
            `passive_seeker`, or absent/`None` if not supplied) -- see
            graph_explainability.py and attach_candidate_segments.
        edges: each `{source, target, weight}`.
    """
    if len(nodes) <= 1:
        st.info("No graph connections found for this entity yet.")
        return

    positions = _radial_positions(nodes)
    max_shared = max((n["shared_skill_count"] or 0) for n in nodes) or 1
    max_weight = max((e["weight"] for e in edges), default=1) or 1

    fig = go.Figure()

    for edge in edges:
        x0, y0 = positions[edge["source"]]
        x1, y1 = positions[edge["target"]]
        width = _EDGE_BASE_WIDTH + _EDGE_WIDTH_RANGE * (edge["weight"] / max_weight)
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[y0, y1], mode="lines",
            line=dict(width=width, color="rgba(150,150,150,0.5)"),
            hoverinfo="skip", showlegend=False,
        ))

    non_center = [n for n in nodes if not n["is_center"]]
    for node_type, color in _TYPE_COLOR.items():
        type_nodes = [n for n in non_center if n["type"] == node_type]
        if not type_nodes:
            continue
        fig.add_trace(go.Scatter(
            x=[positions[n["node_id"]][0] for n in type_nodes],
            y=[positions[n["node_id"]][1] for n in type_nodes],
            mode="markers+text",
            marker=dict(
                size=[_node_size(n, max_shared) for n in type_nodes],
                color=color,
                line=dict(
                    width=[3 if n.get("segment") else 0 for n in type_nodes],
                    color=[
                        _SEGMENT_BORDER_COLOR.get(n.get("segment"), _DEFAULT_BORDER_COLOR)
                        for n in type_nodes
                    ],
                ),
            ),
            text=["" if n["type"] == "Skill" else n["label"] for n in type_nodes],
            textposition="top center",
            hoverinfo="text",
            hovertext=[_node_hover_text(n) for n in type_nodes],
            name=node_type,
        ))

    center = next(n for n in nodes if n["is_center"])
    fig.add_trace(go.Scatter(
        x=[positions[center["node_id"]][0]], y=[positions[center["node_id"]][1]],
        mode="markers+text",
        marker=dict(size=_CENTER_SIZE, color=_CENTER_COLOR, symbol="star", line=dict(width=1, color="black")),
        text=[center["label"]], textposition="top center",
        hoverinfo="text", hovertext=[_node_hover_text(center)],
        name=f"This {center['type'].lower()}",
    ))

    if any(n.get("segment") for n in non_center):
        for segment, border_color in _SEGMENT_BORDER_COLOR.items():
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color="white", line=dict(width=3, color=border_color)),
                name=f"segment: {segment.replace('_', ' ')}", showlegend=True,
            ))

    fig.update_layout(
        showlegend=True, height=600, margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False, scaleanchor="y", scaleratio=1), yaxis=dict(visible=False),
        legend=dict(orientation="h", y=-0.05),
    )
    st.plotly_chart(fig, use_container_width=True)
