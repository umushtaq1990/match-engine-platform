"""Decomposition-tree ("drill-down") report for a golden-eval extraction
run: overall F1 -> profession domain -> occupation, built separately for
candidates (golden CVs) and job posts (golden jobs) so each branch's leaf
counts sum to the right totals. A second, parallel tree breaks each
occupation down by skill type (technical/soft) instead of by F1 -- see
"Two trees, two units" below for why these can't be one tree.

Grafana has no hierarchical decomposition-tree visual (sunburst/icicle) --
this is a genuinely different chart type, so it's a standalone report, not
another dashboard panel. Plotly's Icicle chart is the open-source
equivalent of Power BI's/Tableau's decomposition tree: a rectangular,
top-to-bottom hierarchy where box size is example count and box color is
average F1 (red/yellow/green), so a reviewer can see at a glance which
domain/occupation combination is actually dragging the overall score down.
Rendered to a single self-contained HTML file (plotly.js inlined, no CDN,
no server) so it can be emailed or opened by someone with no access to this
stack at all -- a business audience, not an engineer with a terminal.

golden-dataset v2: `Occupation.domain` is now a real persisted column
(app/ingestion/source_models.py), populated for the curated occupations by
app/golden/seed_curated_occupations.py -- this report used to reconstruct
domain on the fly from occupation_skills, including a hand-curated
Python-side map and padding-title canonicalization (a real bug that work
found and fixed: random per-occupation-row domain anchoring let the same
profession, e.g. "Welder," scatter across multiple domains). golden
selection now only ever assigns candidates/jobs to the new curated
occupations (app/golden/select.py's select_and_assign_golden_candidates),
which have clean, non-padded titles and a correct domain already, so none
of that reconstruction logic is needed anymore -- this just reads the
columns.

Two trees, two units: the first tree's leaves are *examples* (one CV or
one job posting), so "F1" and "count of examples" are well-defined at
every level. Skill type (technical/soft) is a property of a *matched
skill*, not of a whole example -- a single CV legitimately has both
technical and soft skills in its ground truth, so it can't be a strict
child of one occupation node without double-counting or picking an
arbitrary "primary" type. The second tree's leaves are instead *matched
skill mentions* (from match_detail), so "how many technical vs. soft
skills did this occupation's examples successfully match, and at what
average confidence" is its own, internally consistent hierarchy.

Run with: docker compose run --rm golden-eval-tree [--run-id N]
"""
import argparse
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy import select

from app.db import SessionLocal
from app.extraction.models import ExtractionExampleResult, ExtractionRun
from app.ingestion.source_models import Candidate, Job, Occupation, Skill

OUTPUT_DIR = Path("data/golden/reports")
_UNKNOWN = "unknown"


def _pick_default_run_id(session) -> int:
    row = session.execute(select(ExtractionRun.id).order_by(ExtractionRun.f1_avg.desc()).limit(1)).first()
    if row is None:
        raise SystemExit("No extraction_runs found -- pass --run-id explicitly.")
    return row[0]


def _example_rows(session, run_id: int):
    """Returns (source, example_id, f1, match_detail) for every example in the run."""
    return session.execute(
        select(
            ExtractionExampleResult.source,
            ExtractionExampleResult.example_id,
            ExtractionExampleResult.f1,
            ExtractionExampleResult.match_detail,
        ).where(ExtractionExampleResult.run_id == run_id)
    ).all()


def _build_rows(session, run_id: int) -> tuple[list[dict], list[dict]]:
    """Returns (example_rows, skill_match_rows) -- the two different units
    the two trees are built from (see module docstring)."""
    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    occupation_title = dict(session.execute(select(Occupation.id, Occupation.title)).all())
    candidate_occupation = dict(session.execute(select(Candidate.id, Candidate.primary_occupation_id)).all())
    job_occupation = dict(session.execute(select(Job.id, Job.occupation_id)).all())
    skill_type_by_id = dict(session.execute(select(Skill.id, Skill.skill_type)).all())

    example_rows, skill_match_rows = [], []
    for source, example_id, f1, match_detail in _example_rows(session, run_id):
        raw_id = int(example_id.split(":", 1)[1])
        if source == "golden_cv":
            entity_type, occupation_id = "Candidates", candidate_occupation.get(raw_id)
        elif source == "golden_job":
            entity_type, occupation_id = "Job Posts", job_occupation.get(raw_id)
        else:
            continue  # hand-authored/non-golden examples aren't part of this breakdown

        domain = occupation_domain.get(occupation_id) or _UNKNOWN
        occupation = occupation_title.get(occupation_id, _UNKNOWN)
        example_rows.append({
            "entity_type": entity_type, "domain": domain, "occupation": occupation, "f1": float(f1),
        })

        for match in match_detail or []:
            skill_match_rows.append({
                "entity_type": entity_type, "domain": domain, "occupation": occupation,
                "skill_type": skill_type_by_id.get(match["skill_id"]) or _UNKNOWN,
                "confidence": float(match["confidence"]),
            })
    return example_rows, skill_match_rows


def _aggregate(rows: list[dict], value_key: str, *group_keys: str) -> dict[tuple, tuple[int, float]]:
    """group key -> (count, avg of value_key)."""
    buckets: dict[tuple, list[float]] = {}
    for row in rows:
        key = tuple(row[k] for k in group_keys)
        buckets.setdefault(key, []).append(row[value_key])
    return {key: (len(values), sum(values) / len(values)) for key, values in buckets.items()}


def _icicle_trace(
    rows: list[dict], value_key: str, root_label: str, levels: list[tuple[str, ...]]
) -> go.Icicle:
    """`levels` is [group_keys, ...], outermost first, each one tuple longer
    than the last (e.g. ("entity_type",), ("entity_type", "domain"), ...) --
    each level's node id is built from its own group_keys tuple, parented
    to the previous level's node id, same pattern regardless of depth."""
    ids, labels, parents, values, colors = ["root"], [root_label], [""], [len(rows)], [
        sum(r[value_key] for r in rows) / len(rows) if rows else 0.0
    ]

    parent_ids_by_row_key: dict[tuple, str] = {}
    for depth, group_keys in enumerate(levels):
        aggregated = _aggregate(rows, value_key, *group_keys)
        for key, (count, avg_value) in aggregated.items():
            node_id = "root|" + "|".join(f"{k}:{v}" for k, v in zip(group_keys, key))
            parent_key = key[:-1] if depth > 0 else ()
            parent_id = parent_ids_by_row_key.get(parent_key, "root") if depth > 0 else "root"
            ids.append(node_id)
            labels.append(str(key[-1]))
            parents.append(parent_id)
            values.append(count)
            colors.append(avg_value)
            parent_ids_by_row_key[key] = node_id

    return go.Icicle(
        ids=ids, labels=labels, parents=parents, values=values,
        branchvalues="total",
        marker={"colors": colors, "colorscale": "RdYlGn", "cmin": 0, "cmax": 1, "showscale": True},
        text=[f"{c:.0%}" for c in colors],
        texttemplate="%{label}<br>n=%{value}<br>%{text}",
        tiling={"orientation": "v"},
    )


def _build_figure(example_rows: list[dict], skill_match_rows: list[dict], run_id: int) -> go.Figure:
    fig = make_subplots(
        rows=1, cols=2, specs=[[{"type": "icicle"}, {"type": "icicle"}]],
        subplot_titles=(
            "By F1: domain -> occupation",
            "By skill type: domain -> occupation -> technical/soft",
        ),
    )
    fig.add_trace(
        _icicle_trace(
            example_rows, "f1", f"All examples (run {run_id})",
            [("entity_type",), ("entity_type", "domain"), ("entity_type", "domain", "occupation")],
        ),
        row=1, col=1,
    )
    fig.add_trace(
        _icicle_trace(
            skill_match_rows, "confidence", f"All matched skills (run {run_id})",
            [("entity_type",), ("entity_type", "domain"), ("entity_type", "domain", "occupation"),
             ("entity_type", "domain", "occupation", "skill_type")],
        ),
        row=1, col=2,
    )
    fig.update_layout(title=f"Golden evaluation drill-down -- run {run_id}", margin={"t": 80})
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    session = SessionLocal()
    try:
        run_id = args.run_id or _pick_default_run_id(session)
        run = session.get(ExtractionRun, run_id)
        if run is None:
            raise SystemExit(f"No extraction_runs row with id={run_id}")

        example_rows, skill_match_rows = _build_rows(session, run_id)
        candidate_count = sum(1 for r in example_rows if r["entity_type"] == "Candidates")
        job_count = sum(1 for r in example_rows if r["entity_type"] == "Job Posts")
        print(f"Run {run_id} ({run.llm_model}, sample_size={run.sample_size}, f1_avg={run.f1_avg}):")
        print(f"  Candidates: {candidate_count} examples")
        print(f"  Job Posts:  {job_count} examples")
        print(f"  Matched skill mentions: {len(skill_match_rows)}")

        fig = _build_figure(example_rows, skill_match_rows, run_id)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = args.output or (OUTPUT_DIR / f"evaluation_drilldown_run{run_id}.html")
        fig.write_html(output_path, include_plotlyjs=True, full_html=True)
        print(f"Wrote {output_path}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
