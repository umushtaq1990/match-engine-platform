"""Shared DB-query helpers for the golden-eval drill-down app (two Streamlit
pages: Candidates & Job Posts, Skills) -- both pages need the same run
picker and the same per-example/per-skill-match data, so it lives here
once rather than duplicated per page.

Deliberately read-only and direct-to-Postgres (no HTTP hop through the
extraction/api services) -- this is an internal exploration tool for
people with DB access, not a production-facing path, same spirit as
app/golden/build_evaluation_tree.py which this app's data model mirrors.

CV text is NOT fetched here (would need SeaweedFS credentials in yet
another service for a lower-value payoff) -- job posting text
(`description_raw`) is shown directly since it's already plain text in the
DB; candidate CVs show their resolved skill lists only, not full prose.
This is a documented simplification, not an oversight -- see
docs/architecture/golden-evaluation-dataset.md's drill-down app section.
"""
import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.models import ExtractionExampleResult, ExtractionRun
from app.ingestion.source_models import Candidate, Job, Occupation, Skill

_UNKNOWN = "unknown"


def select_run_in_sidebar(session: Session) -> int | None:
    """Every page calls this, not just the Home page -- session_state
    persists across Streamlit's pages, but someone can open a sub-page's
    URL directly without ever visiting Home first, so each page needs to
    be able to pick (or default to) a run on its own."""
    runs = list_runs(session)
    if runs.empty:
        st.sidebar.warning("No extraction_runs found -- run golden-extraction-eval first.")
        return None

    labels = [
        f"run {r.run_id} -- {r.llm_model} -- n={r.sample_size} -- f1={r.f1_avg}"
        for r in runs.itertuples()
    ]
    default_index = 0
    if "run_id" in st.session_state:
        matching = runs.index[runs["run_id"] == st.session_state["run_id"]]
        if len(matching):
            default_index = runs.index.get_loc(matching[0])
    choice = st.sidebar.selectbox("Eval run", labels, index=default_index, key="run_label_sidebar")
    run_id = int(runs.iloc[labels.index(choice)]["run_id"])
    st.session_state["run_id"] = run_id
    return run_id


def list_runs(session: Session) -> pd.DataFrame:
    rows = session.execute(
        select(
            ExtractionRun.id, ExtractionRun.llm_model, ExtractionRun.run_at,
            ExtractionRun.sample_size, ExtractionRun.f1_avg,
        ).order_by(ExtractionRun.id.desc())
    ).all()
    return pd.DataFrame(rows, columns=["run_id", "llm_model", "run_at", "sample_size", "f1_avg"])


def _skill_names(skill_ids, name_by_id: dict[int, str]) -> str:
    return ", ".join(sorted(name_by_id.get(i, f"#{i}") for i in (skill_ids or [])))


def load_example_rows(session: Session, run_id: int) -> pd.DataFrame:
    """One row per golden CV/job example in the run -- entity type, domain,
    occupation, scores, and resolved expected/matched/unmatched skills."""
    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    occupation_title = dict(session.execute(select(Occupation.id, Occupation.title)).all())
    candidate_occupation = dict(session.execute(select(Candidate.id, Candidate.primary_occupation_id)).all())
    job_occupation = dict(session.execute(select(Job.id, Job.occupation_id)).all())
    job_title = dict(session.execute(select(Job.id, Job.title)).all())
    job_description = dict(session.execute(select(Job.id, Job.description_raw)).all())
    name_by_id = dict(session.execute(select(Skill.id, Skill.name)).all())

    results = session.execute(
        select(ExtractionExampleResult).where(ExtractionExampleResult.run_id == run_id)
    ).scalars().all()

    rows = []
    for r in results:
        raw_id = int(r.example_id.split(":", 1)[1])
        if r.source == "golden_cv":
            entity_type, occupation_id = "Candidates", candidate_occupation.get(raw_id)
            title, text_preview = "Candidate CV", None
        elif r.source == "golden_job":
            entity_type, occupation_id = "Job Posts", job_occupation.get(raw_id)
            title = job_title.get(raw_id, _UNKNOWN)
            text_preview = (job_description.get(raw_id) or "")[:500]
        else:
            continue

        matched_names = sorted({
            name_by_id.get(m["skill_id"], f"#{m['skill_id']}") for m in (r.match_detail or [])
        })
        unmatched_phrases = sorted({u["phrase"] for u in (r.unmatched_detail or [])})

        rows.append({
            "example_id": r.example_id,
            "entity_type": entity_type,
            "domain": occupation_domain.get(occupation_id) or _UNKNOWN,
            "occupation": occupation_title.get(occupation_id, _UNKNOWN),
            "title": title,
            "precision": float(r.precision),
            "recall": float(r.recall),
            "f1": float(r.f1),
            "expected_skills": _skill_names(r.expected_skill_ids, name_by_id),
            "matched_skills": ", ".join(matched_names),
            "unmatched_phrases": ", ".join(unmatched_phrases),
            "text_preview": text_preview,
        })
    return pd.DataFrame(rows)


def load_skill_match_rows(session: Session, run_id: int) -> pd.DataFrame:
    """One row per matched skill mention (from match_detail) -- the finer
    unit the "Skills" page drills into (a single example can have both
    technical and soft matches, so this can't share example_rows' frame)."""
    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    occupation_title = dict(session.execute(select(Occupation.id, Occupation.title)).all())
    candidate_occupation = dict(session.execute(select(Candidate.id, Candidate.primary_occupation_id)).all())
    job_occupation = dict(session.execute(select(Job.id, Job.occupation_id)).all())
    skill_name_type = dict(session.execute(select(Skill.id, Skill.name)).all())
    skill_type_by_id = dict(session.execute(select(Skill.id, Skill.skill_type)).all())

    results = session.execute(
        select(
            ExtractionExampleResult.source, ExtractionExampleResult.example_id,
            ExtractionExampleResult.match_detail,
        ).where(ExtractionExampleResult.run_id == run_id)
    ).all()

    rows = []
    for source, example_id, match_detail in results:
        raw_id = int(example_id.split(":", 1)[1])
        if source == "golden_cv":
            entity_type, occupation_id = "Candidates", candidate_occupation.get(raw_id)
        elif source == "golden_job":
            entity_type, occupation_id = "Job Posts", job_occupation.get(raw_id)
        else:
            continue

        domain = occupation_domain.get(occupation_id) or _UNKNOWN
        occupation = occupation_title.get(occupation_id, _UNKNOWN)
        for m in match_detail or []:
            rows.append({
                "example_id": example_id,
                "entity_type": entity_type,
                "domain": domain,
                "occupation": occupation,
                "skill": skill_name_type.get(m["skill_id"], f"#{m['skill_id']}"),
                "skill_type": skill_type_by_id.get(m["skill_id"]) or _UNKNOWN,
                "confidence": float(m["confidence"]),
                "requirement_level": m.get("requirement_level"),
                "source_phrase": m.get("source_phrase"),
            })
    return pd.DataFrame(rows)
