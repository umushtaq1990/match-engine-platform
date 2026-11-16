"""Decomposition-tree ("drill-down") report for a golden-eval extraction
run: overall F1 -> profession domain -> occupation, built separately for
candidates (golden CVs, 500 total) and job posts (golden jobs, 1500 total)
so each branch's leaf counts sum to exactly those numbers.

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

Occupations have no persisted "domain" column -- SKILL_DOMAINS
(app/ingestion/generate_synthetic_data.py) only maps *skill category* to a
coarse domain. This derives each occupation's domain the same way the
generator derives a candidate's anchor domain: the domain that contains the
plurality of that occupation's own occupation_skills, by category. Ties
broken by SKILL_DOMAINS's own iteration order, deterministically.

Run with: docker compose run --rm golden-eval-tree [--run-id N]
"""
import argparse
import re
from collections import Counter
from pathlib import Path

import plotly.graph_objects as go
from sqlalchemy import select

from app.db import SessionLocal
from app.extraction.models import ExtractionExampleResult, ExtractionRun
from app.ingestion.generate_synthetic_data import SKILL_DOMAINS
from app.ingestion.source_models import Candidate, Job, Occupation, OccupationSkill, Skill

OUTPUT_DIR = Path("data/golden/reports")

_CATEGORY_TO_DOMAIN = {
    category: domain for domain, categories in SKILL_DOMAINS.items() for category in categories
}
_UNCATEGORIZED_DOMAIN = "uncategorized"

# The seed data's own occupation_skills domain anchor is picked uniformly at
# random per occupation (generate_synthetic_data.py's
# _assign_domain_anchored_skills -> rng.choice(domain_names)), with no
# semantic link to the occupation's title -- it exists purely to give the
# *skill co-occurrence graph* real community structure (docs/knowledge-
# graph.md), not to encode a real professional taxonomy. Confirmed live:
# before this hand-curated map existed, "Welder" came back in every one of
# the 4 domains depending on which padding row happened to get sampled,
# which would read as a bug to a business audience even though it's
# faithfully reflecting the seed data. For the 26 real titles
# (OCCUPATION_TITLES), use an explicit, human-judged mapping instead; the
# skill-category-majority vote below is still the fallback for anything not
# in this list (e.g. user-added occupations like "AI Engineer").
_CURATED_OCCUPATION_DOMAINS = {
    "Software Engineer": "software_engineering",
    "Data Analyst": "software_engineering",
    "Data Scientist": "software_engineering",
    "Network Administrator": "software_engineering",
    "Security Analyst": "software_engineering",
    "QA Engineer": "software_engineering",
    "Scrum Master": "software_engineering",
    "Project Manager": "business_ops",
    "Sales Representative": "business_ops",
    "Customer Service Agent": "business_ops",
    "Accountant": "business_ops",
    "HR Coordinator": "business_ops",
    "Supply Chain Analyst": "business_ops",
    "Electrician": "trades_logistics",
    "Plumber": "trades_logistics",
    "Warehouse Associate": "trades_logistics",
    "Welder": "trades_logistics",
    "Carpenter": "trades_logistics",
    "Forklift Operator": "trades_logistics",
    "Manufacturing Technician": "trades_logistics",
    "Mechanical Engineer": "trades_logistics",
    "Registered Nurse": "people_services",
    "Graphic Designer": "people_services",
    "Content Writer": "people_services",
    "Teacher": "people_services",
    "Marketing Specialist": "people_services",
}

# Occupations are seeded by cycling a short list of real titles with a
# " <n>" suffix once the real list is exhausted (generate_synthetic_data.py:
# `f"{title}{suffix}"`, suffix e.g. " 4") to reach the target row count --
# "QA Engineer 4" and "QA Engineer" are the *same* profession for a business
# rollup, just two different padding rows. Without this, the occupation
# level of the tree fragments into ~hundreds of near-duplicate 1-5-count
# leaves instead of the handful of real professions a business audience
# actually recognizes (confirmed live: the first version of this report did
# exactly that before this normalization was added).
_PADDING_SUFFIX = re.compile(r" \d+$")


def _canonical_occupation_title(title: str) -> str:
    return _PADDING_SUFFIX.sub("", title)


def _pick_default_run_id(session) -> int:
    row = session.execute(
        select(ExtractionRun.id)
        .where(ExtractionRun.sample_size == 2000)
        .order_by(ExtractionRun.f1_avg.desc())
        .limit(1)
    ).first()
    if row is None:
        raise SystemExit("No extraction_runs with sample_size=2000 found -- pass --run-id explicitly.")
    return row[0]


def _occupation_domains(session) -> dict[int, str]:
    """occupation_id -> domain. Votes are pooled by *canonical* title, not
    raw occupation_id: each padding duplicate ("Welder 4") got its own,
    independently sampled occupation_skills, so voting per-row let the same
    real profession scatter across multiple domains by chance (confirmed
    live: "Welder" showed up under all 4 domains in the first version of
    this report). Pooling by canonical title gives one consistent domain per
    real profession instead."""
    rows = session.execute(
        select(Occupation.id, Occupation.title, Skill.category)
        .join(OccupationSkill, OccupationSkill.occupation_id == Occupation.id)
        .join(Skill, Skill.id == OccupationSkill.skill_id)
    ).all()

    categories_by_title: dict[str, list[str | None]] = {}
    title_by_occupation: dict[int, str] = {}
    for occupation_id, title, category in rows:
        canonical = _canonical_occupation_title(title)
        title_by_occupation[occupation_id] = canonical
        categories_by_title.setdefault(canonical, []).append(category)

    domain_by_title: dict[str, str] = {}
    for canonical, categories in categories_by_title.items():
        if canonical in _CURATED_OCCUPATION_DOMAINS:
            domain_by_title[canonical] = _CURATED_OCCUPATION_DOMAINS[canonical]
            continue
        domain_votes = Counter(
            _CATEGORY_TO_DOMAIN.get(c, _UNCATEGORIZED_DOMAIN) for c in categories
        )
        domain_by_title[canonical] = domain_votes.most_common(1)[0][0]

    return {
        occupation_id: domain_by_title[canonical]
        for occupation_id, canonical in title_by_occupation.items()
    }


def _example_rows(session, run_id: int) -> list[tuple[str, str, float]]:
    """Returns (source, example_id, f1) for every example in the run."""
    return session.execute(
        select(
            ExtractionExampleResult.source,
            ExtractionExampleResult.example_id,
            ExtractionExampleResult.f1,
        ).where(ExtractionExampleResult.run_id == run_id)
    ).all()


def _build_tree_rows(session, run_id: int) -> list[dict]:
    occupation_domain = _occupation_domains(session)
    occupation_title = dict(session.execute(select(Occupation.id, Occupation.title)).all())
    candidate_occupation = dict(
        session.execute(select(Candidate.id, Candidate.primary_occupation_id)).all()
    )
    job_occupation = dict(session.execute(select(Job.id, Job.occupation_id)).all())

    rows = []
    for source, example_id, f1 in _example_rows(session, run_id):
        raw_id = int(example_id.split(":", 1)[1])
        if source == "golden_cv":
            entity_type, occupation_id = "Candidates", candidate_occupation.get(raw_id)
        elif source == "golden_job":
            entity_type, occupation_id = "Job Posts", job_occupation.get(raw_id)
        else:
            continue  # hand-authored/non-golden examples aren't part of this breakdown

        domain = occupation_domain.get(occupation_id, _UNCATEGORIZED_DOMAIN)
        title = _canonical_occupation_title(occupation_title.get(occupation_id, "Unknown occupation"))
        rows.append({"entity_type": entity_type, "domain": domain, "occupation": title, "f1": float(f1)})
    return rows


def _aggregate(rows: list[dict], *keys: str) -> dict[tuple, tuple[int, float]]:
    """group key -> (count, avg_f1)."""
    buckets: dict[tuple, list[float]] = {}
    for row in rows:
        key = tuple(row[k] for k in keys)
        buckets.setdefault(key, []).append(row["f1"])
    return {key: (len(f1s), sum(f1s) / len(f1s)) for key, f1s in buckets.items()}


def _build_icicle(rows: list[dict], run_id: int, f1_avg_overall: float) -> go.Figure:
    by_type = _aggregate(rows, "entity_type")
    by_type_domain = _aggregate(rows, "entity_type", "domain")
    by_type_domain_occupation = _aggregate(rows, "entity_type", "domain", "occupation")

    ids, labels, parents, values, colors = [], [], [], [], []

    root_id = "All"
    ids.append(root_id)
    labels.append(f"All golden examples (run {run_id})")
    parents.append("")
    values.append(len(rows))
    colors.append(f1_avg_overall)

    for (entity_type,), (count, avg_f1) in by_type.items():
        node_id = f"type:{entity_type}"
        ids.append(node_id)
        labels.append(entity_type)
        parents.append(root_id)
        values.append(count)
        colors.append(avg_f1)

    for (entity_type, domain), (count, avg_f1) in by_type_domain.items():
        node_id = f"type:{entity_type}|domain:{domain}"
        ids.append(node_id)
        labels.append(domain)
        parents.append(f"type:{entity_type}")
        values.append(count)
        colors.append(avg_f1)

    for (entity_type, domain, occupation), (count, avg_f1) in by_type_domain_occupation.items():
        node_id = f"type:{entity_type}|domain:{domain}|occ:{occupation}"
        ids.append(node_id)
        labels.append(occupation)
        parents.append(f"type:{entity_type}|domain:{domain}")
        values.append(count)
        colors.append(avg_f1)

    fig = go.Figure(go.Icicle(
        ids=ids, labels=labels, parents=parents, values=values,
        branchvalues="total",
        marker={"colors": colors, "colorscale": "RdYlGn", "cmin": 0, "cmax": 1, "showscale": True},
        text=[f"{c:.0%}" for c in colors],
        texttemplate="%{label}<br>n=%{value}<br>F1=%{text}",
        hovertemplate="%{label}<br>examples: %{value}<br>avg F1: %{customdata:.4f}<extra></extra>",
        customdata=colors,
        tiling={"orientation": "v"},
    ))
    fig.update_layout(
        title=f"Golden evaluation drill-down -- run {run_id} (overall F1 {f1_avg_overall:.4f})",
        margin={"t": 60, "l": 10, "r": 10, "b": 10},
    )
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

        rows = _build_tree_rows(session, run_id)
        candidate_count = sum(1 for r in rows if r["entity_type"] == "Candidates")
        job_count = sum(1 for r in rows if r["entity_type"] == "Job Posts")
        print(f"Run {run_id} ({run.llm_model}, sample_size={run.sample_size}, f1_avg={run.f1_avg}):")
        print(f"  Candidates: {candidate_count} examples")
        print(f"  Job Posts:  {job_count} examples")

        fig = _build_icicle(rows, run_id, float(run.f1_avg))

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = args.output or (OUTPUT_DIR / f"evaluation_drilldown_run{run_id}.html")
        fig.write_html(output_path, include_plotlyjs=True, full_html=True)
        print(f"Wrote {output_path}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
