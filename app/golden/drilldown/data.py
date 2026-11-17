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
from collections import Counter
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.models import BehavioralEvent
from app.analytics.segmentation import (
    ACTIVE_CONVERSION_THRESHOLD,
    ACTIVE_EVENT_COUNT_THRESHOLD,
    DORMANT_DAYS_THRESHOLD,
    FREQUENCY_WINDOW_DAYS,
    compute_segment_label,
)
from app.clustering.models import CandidateSkillCluster, JobSkillCluster
from app.embeddings.models import EntityEmbedding
from app.extraction.models import ExtractionExampleResult, ExtractionRun
from app.golden.generate_persona_events import assign_personas
from app.golden.models import GoldenInjectedNoise
from app.golden.select import select_existing_golden_candidate_ids, select_existing_golden_job_ids
from app.ingestion.source_models import Candidate, CandidateSkill, Job, JobSkill, Occupation, Skill

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


def mlflow_base_url_in_sidebar() -> str:
    """Same purpose as the Grafana dashboards' MLFLOW_BASE_URL variable --
    a per-viewer override since this environment's real MLflow host isn't
    knowable in code, defaulting to local dev."""
    return st.sidebar.text_input("MLflow base URL", value="http://localhost:5000", key="mlflow_base_url")


def mlflow_trace_url(base_url: str, trace_id: str | None) -> str | None:
    if not trace_id:
        return None
    return f"{base_url}/#/experiments/0/traces?selectedTraceId={trace_id}"


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
    occupation, scores, resolved expected/matched/unmatched skills, and
    full-traceability columns: mlflow_trace_id (this exact call's trace --
    None for runs scored before that column existed) and whether this
    example was one of app/golden/inject_noise.py's deliberately
    corrupted ones (and which skill was injected, so a reviewer can
    immediately see "this low score is intentional noise," not a real
    extraction miss)."""
    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    occupation_title = dict(session.execute(select(Occupation.id, Occupation.title)).all())
    candidate_occupation = dict(session.execute(select(Candidate.id, Candidate.primary_occupation_id)).all())
    job_occupation = dict(session.execute(select(Job.id, Job.occupation_id)).all())
    job_title = dict(session.execute(select(Job.id, Job.title)).all())
    job_description = dict(session.execute(select(Job.id, Job.description_raw)).all())
    name_by_id = dict(session.execute(select(Skill.id, Skill.name)).all())
    noise_skill_by_candidate = dict(session.execute(
        select(GoldenInjectedNoise.candidate_id, GoldenInjectedNoise.skill_id)
        .where(GoldenInjectedNoise.candidate_id.isnot(None))
    ).all())
    noise_skill_by_job = dict(session.execute(
        select(GoldenInjectedNoise.job_id, GoldenInjectedNoise.skill_id)
        .where(GoldenInjectedNoise.job_id.isnot(None))
    ).all())

    results = session.execute(
        select(ExtractionExampleResult).where(ExtractionExampleResult.run_id == run_id)
    ).scalars().all()

    rows = []
    for r in results:
        raw_id = int(r.example_id.split(":", 1)[1])
        if r.source == "golden_cv":
            entity_type, occupation_id = "Candidates", candidate_occupation.get(raw_id)
            title, text_preview = "Candidate CV", None
            injected_skill_id = noise_skill_by_candidate.get(raw_id)
        elif r.source == "golden_job":
            entity_type, occupation_id = "Job Posts", job_occupation.get(raw_id)
            title = job_title.get(raw_id, _UNKNOWN)
            text_preview = (job_description.get(raw_id) or "")[:500]
            injected_skill_id = noise_skill_by_job.get(raw_id)
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
            "mlflow_trace_id": r.mlflow_trace_id,
            "injected_noise_skill": name_by_id.get(injected_skill_id) if injected_skill_id else None,
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
            ExtractionExampleResult.match_detail, ExtractionExampleResult.mlflow_trace_id,
        ).where(ExtractionExampleResult.run_id == run_id)
    ).all()

    rows = []
    for source, example_id, match_detail, mlflow_trace_id in results:
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
                "mlflow_trace_id": mlflow_trace_id,
                "skill": skill_name_type.get(m["skill_id"], f"#{m['skill_id']}"),
                "skill_type": skill_type_by_id.get(m["skill_id"]) or _UNKNOWN,
                "confidence": float(m["confidence"]),
                "requirement_level": m.get("requirement_level"),
                "source_phrase": m.get("source_phrase"),
            })
    return pd.DataFrame(rows)


# For display only -- compute_segment_label itself is the single source of
# truth for the actual thresholds; this is just so the page can show a
# reader what "active_seeker" etc. actually mean without hardcoding the
# numbers a second time.
SEGMENT_THRESHOLDS_CAPTION = (
    f"dormant: no event in the last {DORMANT_DAYS_THRESHOLD} days. "
    f"active_seeker: >= {ACTIVE_EVENT_COUNT_THRESHOLD} events in the last {FREQUENCY_WINDOW_DAYS} days, "
    f"or >= {ACTIVE_CONVERSION_THRESHOLD:.0%} shown->applied conversion. "
    "passive_seeker: present, but neither of those."
)


def load_behavioral_rows(session: Session) -> pd.DataFrame:
    """One row per golden candidate: domain/occupation (same taxonomy as
    the other two pages), the persona it was deliberately assigned
    (app/golden/generate_persona_events.py -- a pure, deterministic
    function of the candidate id list, re-derived here rather than stored
    anywhere), and the *actual* rule-based segment
    (app/analytics/segmentation.py) that behavior produced -- the point
    being to compare "what we intended" against "what the real
    segmentation logic, unchanged, actually says" for the same data.

    Computed as one bulk GROUP BY query, not compute_actor_features()
    called once per candidate (which would be 1000 round trips) --
    reuses that function's own SQL shape, just batched.
    """
    golden_ids = select_existing_golden_candidate_ids(session)
    personas = assign_personas(golden_ids)

    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    occupation_title = dict(session.execute(select(Occupation.id, Occupation.title)).all())
    candidate_occupation = dict(
        session.execute(
            select(Candidate.id, Candidate.primary_occupation_id).where(Candidate.id.in_(golden_ids))
        ).all()
    )

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=FREQUENCY_WINDOW_DAYS)
    agg_rows = session.execute(
        select(
            BehavioralEvent.actor_id,
            func.max(BehavioralEvent.received_at).label("last_event_at"),
            func.count().filter(BehavioralEvent.received_at >= window_start).label("event_count_window"),
            func.count().filter(BehavioralEvent.event_type == "recommendation_shown").label("shown_count"),
            func.count().filter(BehavioralEvent.event_type == "job_applied").label("applied_count"),
        )
        .where(BehavioralEvent.actor_type == "candidate", BehavioralEvent.actor_id.in_(golden_ids))
        .group_by(BehavioralEvent.actor_id)
    ).all()
    agg_by_candidate = {row.actor_id: row for row in agg_rows}

    rows = []
    for candidate_id in golden_ids:
        occupation_id = candidate_occupation.get(candidate_id)
        agg = agg_by_candidate.get(candidate_id)

        if agg is None:
            days_since_last_event, event_count_window, shown_count, applied_count = None, 0, 0, 0
        else:
            last_event_at = agg.last_event_at
            if last_event_at is not None and last_event_at.tzinfo is None:
                last_event_at = last_event_at.replace(tzinfo=timezone.utc)
            days_since_last_event = (now - last_event_at).days if last_event_at is not None else None
            event_count_window = agg.event_count_window
            shown_count = agg.shown_count
            applied_count = agg.applied_count

        conversion_rate = (applied_count / shown_count) if shown_count > 0 else None
        segment = compute_segment_label(days_since_last_event, event_count_window, conversion_rate)

        rows.append({
            "candidate_id": candidate_id,
            "domain": occupation_domain.get(occupation_id) or _UNKNOWN,
            "occupation": occupation_title.get(occupation_id, _UNKNOWN),
            "persona": personas.get(candidate_id, _UNKNOWN),
            "segment": segment,
            "days_since_last_event": days_since_last_event,
            "event_count_window": event_count_window,
            "shown_count": shown_count,
            "applied_count": applied_count,
            "conversion_rate": round(conversion_rate, 4) if conversion_rate is not None else None,
        })
    return pd.DataFrame(rows)


# segmentation.py's own docstring calls this "v3, only if the rule-based
# baseline is shown insufficient" -- built here as the actual check, not
# assumed either way: does an unsupervised pass over the same KPIs find
# structure that lines up with the personas golden-dataset v2 deliberately
# assigned, or something genuinely different? k=5 chosen to match the
# number of personas directly, so the comparison is apples-to-apples.
CLUSTER_FEATURE_COLUMNS = [
    "days_since_last_event", "event_count_window", "shown_count", "applied_count", "conversion_rate",
]
DEFAULT_CLUSTER_COUNT = 5


def _scaled_behavioral_features(rows: pd.DataFrame):
    """Shared by compute_behavioral_clusters and compute_k_diagnostics (via
    the page) -- factored out so the elbow/silhouette sweep is checked
    against the exact same feature prep the real fit uses, not a
    close-enough approximation. `days_since_last_event` is None for
    candidates with zero events (dormant_never_engaged who got unlucky on
    the 50% coin flip) -- imputed to a large constant (far outside the
    real 1-150 day range observed) rather than 0, since 0 would wrongly
    read as "active today." Standardized (StandardScaler) since the five
    KPIs are on wildly different scales (days: 0-150, conversion rate:
    0-1) and k-means is distance-based.
    """
    from sklearn.preprocessing import StandardScaler

    features = rows[CLUSTER_FEATURE_COLUMNS].copy()
    features["days_since_last_event"] = features["days_since_last_event"].fillna(999)
    features["conversion_rate"] = features["conversion_rate"].fillna(0)
    return StandardScaler().fit_transform(features.to_numpy())


def compute_behavioral_clusters(rows: pd.DataFrame, k: int = DEFAULT_CLUSTER_COUNT) -> pd.Series:
    """K-means over the same behavioral KPIs segmentation.py's rule-based
    labels are built from -- see _scaled_behavioral_features for the
    feature prep this fits on."""
    from sklearn.cluster import KMeans

    scaled = _scaled_behavioral_features(rows)
    labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(scaled)
    return pd.Series([f"cluster_{label}" for label in labels], index=rows.index, name="cluster")


@st.cache_data(show_spinner="Fitting k-means across a range of k for the behavioral diagnostic...")
def compute_behavioral_k_diagnostics(rows: pd.DataFrame, k_range=range(2, 13)) -> pd.DataFrame:
    """Same elbow/silhouette check as compute_k_diagnostics, over the
    behavioral KPIs instead of skills embeddings -- fits on
    _scaled_behavioral_features(rows), the exact preprocessing
    compute_behavioral_clusters itself uses, so this sweep is checked
    against what the real fit sees, not a stand-in."""
    return compute_k_diagnostics(_scaled_behavioral_features(rows), k_range=k_range)


# Mirrors app/embeddings/compute_embeddings.py's own DEFAULT_EMBEDDING_MODEL
# -- duplicated as a literal rather than imported so this read-only,
# direct-to-Postgres app (docker/Dockerfile.golden-drilldown) doesn't have
# to pull in that module's ollama/mlflow dependency chain for one string.
_SKILLS_EMBEDDING_MODEL = "all-minilm"


def load_skill_cluster_rows(session: Session) -> pd.DataFrame:
    """One row per golden candidate: domain/occupation (same taxonomy as
    the other pages), the real-time k-means skill cluster + the most
    recent nightly Louvain community it's been linked to
    (app/clustering -- see docs/architecture/data-platform/
    skill-clustering.md), and the raw skills embedding vector itself (for
    this page's t-SNE projection -- never persisted, see
    compute_tsne_projection below).

    Candidates without a skills embedding or cluster assignment yet
    (nightly job hasn't run for them) are dropped, not shown as a
    misleading "unknown" bucket -- same spirit as the other loaders'
    `_UNKNOWN` fallback, but a cluster/vector can't be synthesized the way
    a label can.
    """
    golden_ids = select_existing_golden_candidate_ids(session)

    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    occupation_title = dict(session.execute(select(Occupation.id, Occupation.title)).all())
    candidate_occupation = dict(
        session.execute(
            select(Candidate.id, Candidate.primary_occupation_id).where(Candidate.id.in_(golden_ids))
        ).all()
    )

    cluster_rows = session.execute(
        select(
            CandidateSkillCluster.candidate_id, CandidateSkillCluster.cluster_label,
            CandidateSkillCluster.louvain_community_id,
        ).where(CandidateSkillCluster.candidate_id.in_(golden_ids))
    ).all()
    vector_by_candidate = dict(
        session.execute(
            select(EntityEmbedding.entity_id, EntityEmbedding.vector).where(
                EntityEmbedding.entity_type == "candidate",
                EntityEmbedding.dimension == "skills",
                EntityEmbedding.embedding_model == _SKILLS_EMBEDDING_MODEL,
                EntityEmbedding.entity_id.in_(golden_ids),
            )
        ).all()
    )

    rows = []
    for candidate_id, cluster_label, louvain_community_id in cluster_rows:
        vector = vector_by_candidate.get(candidate_id)
        if vector is None:
            continue
        occupation_id = candidate_occupation.get(candidate_id)
        rows.append({
            "candidate_id": candidate_id,
            "domain": occupation_domain.get(occupation_id) or _UNKNOWN,
            "occupation": occupation_title.get(occupation_id, _UNKNOWN),
            "cluster_label": cluster_label,
            "louvain_community_id": (
                str(louvain_community_id) if louvain_community_id is not None else _UNKNOWN
            ),
            "vector": vector,
        })
    return pd.DataFrame(rows)


def compute_tsne_projection(vectors, random_state: int = 42):
    """2D projection for the scatter plot only -- t-SNE has no `.predict()`
    for a new point (unlike the k-means centroids in
    app/clustering/kmeans_skill_clusters.py), so this is recomputed fresh
    from whatever rows are being shown, never stored, and never part of
    cluster *assignment*."""
    from sklearn.manifold import TSNE

    perplexity = min(30, max(2, len(vectors) - 1))
    coords = TSNE(n_components=2, random_state=random_state, perplexity=perplexity).fit_transform(vectors)
    return coords[:, 0], coords[:, 1]


def name_clusters_by_majority(rows: pd.DataFrame, cluster_col: str, label_col: str) -> dict:
    """Maps each raw cluster id (k-means label, Louvain community id, or
    behavioral cluster id -- all otherwise-opaque to a non-technical
    reader) to a human name: whichever `label_col` value is most common
    among that cluster's members, with the raw id kept in parentheses for
    traceability back to the crosstabs/filters that still key on it. Not
    a claim that every member shares that label -- same "most common,
    stated honestly as a majority, not a unanimous fact" framing as
    app/graph/cluster_entities.py's compute_mean_occupation_purity.
    """
    mapping = {}
    for cluster_id, group in rows.groupby(cluster_col):
        if group[label_col].dropna().empty:
            mapping[cluster_id] = f"Unlabeled ({cluster_id})"
            continue
        top_label = group[label_col].value_counts().idxmax()
        mapping[cluster_id] = f"{top_label} ({cluster_id})"
    return mapping


def link_clusters_by_domain(
    source_rows: pd.DataFrame, source_cluster_col: str, target_rows: pd.DataFrame, target_cluster_col: str,
) -> dict:
    """The cheap, already-available link between independently-fit
    candidate and job clusterings: each is its own k-means fit over its
    own embedding space, never the same cluster by index, but both land
    on the same real domain taxonomy -- so a source cluster links to
    whichever target cluster(s) share its majority domain (plural,
    returned as a list, since the two fits aren't guaranteed to be
    perfectly 1:1, even though they measured out clean here). See
    docs/architecture/data-platform/skill-clustering.md.
    """
    source_domain = {
        cid: g["domain"].value_counts().idxmax() for cid, g in source_rows.groupby(source_cluster_col)
    }
    target_domain = {
        cid: g["domain"].value_counts().idxmax() for cid, g in target_rows.groupby(target_cluster_col)
    }
    return {
        s_cluster: [t for t, t_domain in target_domain.items() if t_domain == s_domain]
        for s_cluster, s_domain in source_domain.items()
    }


@st.cache_data(show_spinner="Fitting k-means across a range of k for the elbow/silhouette diagnostic...")
def compute_k_diagnostics(vectors, k_range=range(2, 13)) -> pd.DataFrame:
    """Elbow (inertia) + silhouette score per k -- the actual justification
    for app/clustering/kmeans_skill_clusters.py's DEFAULT_K_BY_ENTITY_TYPE
    (either entity type -- just pass that type's vectors), measured here
    rather than assumed. Deliberately lives in this read-only dashboard
    module, not in kmeans_skill_clusters.py itself:
    it only needs numpy/sklearn over vectors already in hand, and pulling
    in kmeans_skill_clusters.py's fit/assign machinery would drag
    refresh_entity's ollama/mlflow dependency chain into this lightweight,
    direct-to-Postgres app (docker/Dockerfile.golden-drilldown) for
    nothing this function needs.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    rows = []
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=42, n_init=10).fit(vectors)
        rows.append({
            "k": k, "inertia": km.inertia_,
            "silhouette": silhouette_score(vectors, km.labels_),
        })
    return pd.DataFrame(rows)


def load_candidate_skill_names(session: Session, candidate_ids: list[int]) -> pd.DataFrame:
    """One row per (candidate, skill) pair -- real `candidate_skills` ground
    truth (positive evidence only, see CandidateSkill's own docstring),
    for the "which skills are actually trending" sections on the Skill
    Clusters 360 page. Joined by name here, not skill_id, so the caller
    never needs a second lookup."""
    rows = session.execute(
        select(CandidateSkill.candidate_id, Skill.name)
        .join(Skill, Skill.id == CandidateSkill.skill_id)
        .where(CandidateSkill.candidate_id.in_(candidate_ids))
    ).all()
    return pd.DataFrame(rows, columns=["candidate_id", "skill_name"])


def compute_top_skills(skill_rows: pd.DataFrame, population_size: int, top_n: int = 10) -> pd.DataFrame:
    """Top N skills by how many candidates (in whatever population
    `skill_rows` already represents -- overall, or pre-filtered to one
    cluster) have them, with the real proportion of that population, not
    just a raw count that reads differently for a cluster of 150 vs 500.
    """
    if skill_rows.empty or population_size == 0:
        return pd.DataFrame(columns=["skill_name", "count", "proportion"])
    counts = skill_rows["skill_name"].value_counts().head(top_n)
    return pd.DataFrame({
        "skill_name": counts.index, "count": counts.to_numpy(),
        "proportion": counts.to_numpy() / population_size,
    })


def generate_skill_wordcloud(skill_counts: "pd.Series", width: int = 600, height: int = 450):
    """A word cloud sized by how many candidates have each skill -- same
    underlying counts as compute_top_skills, just a visual, not a second
    source of truth. Returns a PIL Image (via WordCloud.to_image(), no
    matplotlib pyplot call needed) for st.image to render directly.
    Default width/height is taller than wide (600x450, not the library's
    wide default) so it fills a narrower column's height rather than
    leaving empty space below it next to a taller bar chart."""
    from wordcloud import WordCloud

    wc = WordCloud(width=width, height=height, background_color="white", colormap="viridis")
    return wc.generate_from_frequencies(skill_counts.to_dict()).to_image()


def load_candidate_skill_detail(session: Session, candidate_id: int) -> pd.DataFrame:
    """One candidate's own skills with proficiency -- load_candidate_skill_names
    (name-only, built for the population-level trending-skills section)
    doesn't carry this; the Candidate Profile 360 page's skills table
    needs it."""
    rows = session.execute(
        select(Skill.name, CandidateSkill.proficiency)
        .join(Skill, Skill.id == CandidateSkill.skill_id)
        .where(CandidateSkill.candidate_id == candidate_id)
        .order_by(Skill.name)
    ).all()
    return pd.DataFrame(
        [{"skill_name": name, "proficiency": proficiency.value} for name, proficiency in rows]
    )


def load_job_skill_detail(session: Session, job_id: int) -> pd.DataFrame:
    """One job's own required skills with requirement level -- job-side
    mirror of load_candidate_skill_detail, for the Job Profile 360
    page's skills table."""
    rows = session.execute(
        select(Skill.name, JobSkill.requirement_level)
        .join(Skill, Skill.id == JobSkill.skill_id)
        .where(JobSkill.job_id == job_id)
        .order_by(Skill.name)
    ).all()
    return pd.DataFrame(
        [{"skill_name": name, "requirement_level": level.value} for name, level in rows]
    )


def load_job_occupation_titles(session: Session, job_ids: list[int]) -> dict[int, str]:
    """job_id -> occupation title, for labeling the matching-jobs table on
    the Candidate Profile 360 page -- MatchResult.label is already the
    job title, this is the one extra lookup it doesn't carry."""
    rows = session.execute(
        select(Job.id, Occupation.title)
        .join(Occupation, Occupation.id == Job.occupation_id, isouter=True)
        .where(Job.id.in_(job_ids))
    ).all()
    return {job_id: title or _UNKNOWN for job_id, title in rows}


def load_candidate_demographics(session: Session, candidate_ids: list[int]) -> pd.DataFrame:
    """Name/location/years_experience for the Candidate Profile 360 page's
    header -- `years_experience`, not age, is shown labeled as such: no
    age field exists anywhere in this schema (confirmed), and no photo/
    avatar field either (the page draws a decorative initials avatar
    instead, not a real image)."""
    rows = session.execute(
        select(
            Candidate.id, Candidate.full_name, Candidate.location_city,
            Candidate.location_region, Candidate.years_experience,
        ).where(Candidate.id.in_(candidate_ids))
    ).all()
    return pd.DataFrame(
        rows, columns=["candidate_id", "full_name", "location_city", "location_region", "years_experience"]
    )


def find_similar_entities(rows: pd.DataFrame, id_col: str, target_id: int, top_n: int = 10) -> pd.DataFrame:
    """Nearest neighbors of `target_id` by Euclidean distance over
    whatever skills-embedding vectors `rows` already carries (works for
    either load_skill_cluster_rows' candidate_id or
    load_job_skill_cluster_rows' job_id -- same method, either entity
    type) -- a genuinely new capability (no candidate-to-candidate or
    job-to-job embedding similarity exists anywhere else in this repo;
    app/graph/cluster_entities.py's *_SIMILAR_TO relationships are
    shared-skill-count based, graph-side only, never queried from here).
    Instant rather than a fresh query, since `rows` is whatever's
    already loaded for the page (the same vectors driving its t-SNE
    plot). Returns same_skill_cluster/same_louvain_community alongside
    the raw distance, so a reader can cross-check this embedding-based
    notion of "similar" against the graph-based one, same two-method
    framing as the rest of this feature.
    """
    import numpy as np

    target_row = rows.loc[rows[id_col] == target_id]
    if target_row.empty:
        return pd.DataFrame(columns=[id_col, "distance", "same_skill_cluster", "same_louvain_community"])
    target = target_row.iloc[0]
    target_vector = np.array(target["vector"], dtype=np.float64)

    others = rows.loc[rows[id_col] != target_id].copy()
    others["distance"] = others["vector"].apply(
        lambda v: float(np.linalg.norm(np.array(v, dtype=np.float64) - target_vector))
    )
    others["same_skill_cluster"] = others["cluster_label"] == target["cluster_label"]
    others["same_louvain_community"] = others["louvain_community_id"] == target["louvain_community_id"]
    return others.sort_values("distance").head(top_n)[[
        id_col, "distance", "same_skill_cluster", "same_louvain_community",
    ]].reset_index(drop=True)


def compute_peer_skill_gap(
    target_skills: set, peer_skill_rows: pd.DataFrame, peer_count: int, top_n: int = 10
) -> pd.DataFrame:
    """The gap-vs-neighbors half of the Candidate Profile 360 page's
    skill-gap section: a frequency table ranking every skill the target's
    ~10 nearest similar candidates have (how many of them have it),
    skills the target already has subtracted out first, top N of
    whatever remains -- same shape (`skill_name, count, proportion`) as
    compute_top_skills, so it can go straight into
    render_top_skills_bar_chart without reshaping.
    """
    if peer_skill_rows.empty or peer_count == 0:
        return pd.DataFrame(columns=["skill_name", "count", "proportion"])
    counts = peer_skill_rows["skill_name"].value_counts()
    counts = counts[~counts.index.isin(target_skills)].head(top_n)
    return pd.DataFrame({
        "skill_name": counts.index, "count": counts.to_numpy(), "proportion": counts.to_numpy() / peer_count,
    }).reset_index(drop=True)


def compute_job_skill_gap(
    target_skills: set, job_required_skill_names: list[set], top_n: int = 10
) -> pd.DataFrame:
    """The gap-vs-matching-jobs half: a popularity ranking of every
    required skill across the candidate's top matching jobs (how many of
    those jobs require it -- `job_required_skill_names` is one set of
    skill names per job, already resolved from
    ScoreBreakdown.matching_required_skill_ids | missing_required_skill_ids,
    the full required set regardless of whether the candidate already has
    each one), skills the target already has subtracted out first, top N
    of whatever remains. Same shape as compute_peer_skill_gap/
    compute_top_skills, for the same bar-chart helper.
    """
    job_count = len(job_required_skill_names)
    if job_count == 0:
        return pd.DataFrame(columns=["skill_name", "count", "proportion"])
    all_required = pd.Series([name for skills in job_required_skill_names for name in skills])
    counts = all_required.value_counts()
    counts = counts[~counts.index.isin(target_skills)].head(top_n)
    return pd.DataFrame({
        "skill_name": counts.index, "count": counts.to_numpy(), "proportion": counts.to_numpy() / job_count,
    }).reset_index(drop=True)


EMERGING_SKILL_PEER_THRESHOLD = 0.2


def compute_skill_market_signal(
    job_gap: pd.DataFrame, peer_skill_rows: pd.DataFrame, peer_count: int,
    emerging_threshold: float = EMERGING_SKILL_PEER_THRESHOLD,
) -> pd.DataFrame:
    """A heuristic, not validated market intelligence (labeled as such
    wherever shown): for each job-demanded skill this candidate is
    missing, what fraction of their similar candidates already have it.
    Low peer adoption + the job demand `job_gap` already establishes
    suggests an emerging/differentiating skill worth learning ahead of
    the curve; high peer adoption suggests a baseline most peers have
    already cleared -- this candidate is behind on it, not ahead of a
    trend. Closes the loop the user asked for: course recommendations
    should be informed by both "what jobs want" and "what my peers
    already have," not just the first alone.
    """
    if job_gap.empty:
        return pd.DataFrame(columns=[
            "skill_name", "job_demand_proportion", "peer_prevalence", "market_signal",
        ])
    peer_counts = peer_skill_rows["skill_name"].value_counts()
    peer_prevalence = job_gap["skill_name"].map(
        lambda name: (peer_counts.get(name, 0) / peer_count) if peer_count else 0.0
    )
    market_signal = peer_prevalence.apply(
        lambda p: "Emerging (rare among peers, in demand)" if p < emerging_threshold
        else "Catching up (common among peers)"
    )
    return pd.DataFrame({
        "skill_name": job_gap["skill_name"], "job_demand_proportion": job_gap["proportion"],
        "peer_prevalence": peer_prevalence, "market_signal": market_signal,
    }).reset_index(drop=True)


# The three candidate-facing event types that make sense as a weekly engagement
# trend here -- excludes "recommendation_clicked"/"job_saved" (real, but a
# secondary signal, not core to "did they log in, see jobs, apply") to keep
# the trend section to the three metrics the user actually asked for.
TREND_EVENT_TYPES = ["login", "recommendation_shown", "job_applied"]
TREND_EVENT_LABELS = {
    "login": "Logins", "recommendation_shown": "Jobs viewed", "job_applied": "Jobs applied",
}
# Bucket 1 = most recent 7 days, bucket 4 = 22-28 days ago -- oldest-to-newest
# order for a left-to-right chronological chart.
WEEK_BUCKET_LABELS = {1: "Last 7 days", 2: "8-14 days ago", 3: "15-21 days ago", 4: "22-28 days ago"}
WEEK_BUCKET_ORDER = [4, 3, 2, 1]


def load_weekly_event_trend(session: Session, actor_ids: list[int], num_weeks: int = 4) -> pd.DataFrame:
    """Raw per-actor-per-week-per-event-type counts over the last
    `num_weeks` x 7 days, bucketed into non-overlapping weekly periods --
    the standard way to show a real trend (up/down/flat), not a
    cumulative "last N days" total that can only grow. Reuses
    `analytics.behavioral_events` directly, the same source every other
    behavioral feature in this project already reads from
    (app/analytics/models.py) -- no new event log, no new infra.
    """
    if not actor_ids:
        return pd.DataFrame(columns=["actor_id", "week_bucket", "event_type", "count"])
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=num_weeks * 7)
    rows = session.execute(
        select(BehavioralEvent.actor_id, BehavioralEvent.event_type, BehavioralEvent.received_at).where(
            BehavioralEvent.actor_type == "candidate",
            BehavioralEvent.actor_id.in_(actor_ids),
            BehavioralEvent.received_at >= window_start,
            BehavioralEvent.event_type.in_(TREND_EVENT_TYPES),
        )
    ).all()
    if not rows:
        return pd.DataFrame(columns=["actor_id", "week_bucket", "event_type", "count"])
    df = pd.DataFrame(rows, columns=["actor_id", "event_type", "received_at"])
    received_at = pd.to_datetime(df["received_at"], utc=True)
    days_ago = (now - received_at).dt.days
    df["week_bucket"] = (days_ago // 7 + 1).clip(upper=num_weeks)
    return df.groupby(["actor_id", "week_bucket", "event_type"]).size().reset_index(name="count")


def compute_weekly_trend_vs_cluster(
    trend_df: pd.DataFrame, candidate_id: int, cluster_actor_ids: list[int], num_weeks: int = 4
) -> pd.DataFrame:
    """Reindexes load_weekly_event_trend's output so every
    (actor, week, event_type) combination exists, a true 0 where nothing
    happened rather than a missing row -- required before averaging, same
    reasoning app/analytics/segmentation.py's own aggregations already
    rely on elsewhere (absence of an event is 0, not unknown). Returns one
    row per (week_bucket, event_type): this candidate's own count, the
    cluster's mean count, and the cluster's population size, so a reader
    can judge how stable that average actually is (a 48-candidate
    cluster's average is noisier than a 510-candidate one's).
    """
    weeks = list(range(1, num_weeks + 1))
    full_index = pd.MultiIndex.from_product(
        [cluster_actor_ids, weeks, TREND_EVENT_TYPES], names=["actor_id", "week_bucket", "event_type"]
    )
    full = (
        trend_df.set_index(["actor_id", "week_bucket", "event_type"])
        .reindex(full_index, fill_value=0)["count"]
        .reset_index()
    )
    cluster_avg = full.groupby(["week_bucket", "event_type"])["count"].mean().reset_index(name="cluster_avg")
    target = full.loc[full["actor_id"] == candidate_id].rename(columns={"count": "candidate_count"})
    merged = target.merge(cluster_avg, on=["week_bucket", "event_type"])
    merged["cluster_size"] = len(cluster_actor_ids)
    return merged[["week_bucket", "event_type", "candidate_count", "cluster_avg", "cluster_size"]]


def load_job_skill_cluster_rows(session: Session) -> pd.DataFrame:
    """Job-side mirror of load_skill_cluster_rows -- one row per golden
    job: domain/occupation, the real-time k-means skill cluster + the
    most recent nightly Louvain community, and the raw skills embedding
    vector for the t-SNE projection. Jobs without a skills embedding or
    cluster assignment yet are dropped, same reasoning as the candidate
    version."""
    golden_ids = select_existing_golden_job_ids(session)

    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    occupation_title = dict(session.execute(select(Occupation.id, Occupation.title)).all())
    job_occupation = dict(
        session.execute(select(Job.id, Job.occupation_id).where(Job.id.in_(golden_ids))).all()
    )

    cluster_rows = session.execute(
        select(
            JobSkillCluster.job_id, JobSkillCluster.cluster_label, JobSkillCluster.louvain_community_id,
        ).where(JobSkillCluster.job_id.in_(golden_ids))
    ).all()
    vector_by_job = dict(
        session.execute(
            select(EntityEmbedding.entity_id, EntityEmbedding.vector).where(
                EntityEmbedding.entity_type == "job",
                EntityEmbedding.dimension == "skills",
                EntityEmbedding.embedding_model == _SKILLS_EMBEDDING_MODEL,
                EntityEmbedding.entity_id.in_(golden_ids),
            )
        ).all()
    )

    rows = []
    for job_id, cluster_label, louvain_community_id in cluster_rows:
        vector = vector_by_job.get(job_id)
        if vector is None:
            continue
        occupation_id = job_occupation.get(job_id)
        rows.append({
            "job_id": job_id,
            "domain": occupation_domain.get(occupation_id) or _UNKNOWN,
            "occupation": occupation_title.get(occupation_id, _UNKNOWN),
            "cluster_label": cluster_label,
            "louvain_community_id": (
                str(louvain_community_id) if louvain_community_id is not None else _UNKNOWN
            ),
            "vector": vector,
        })
    return pd.DataFrame(rows)


def load_job_demographics(session: Session, job_ids: list[int]) -> pd.DataFrame:
    """title/company/location/salary for the drill-down table -- job-side
    mirror of load_candidate_demographics."""
    rows = session.execute(
        select(
            Job.id, Job.title, Job.company_name, Job.location_city, Job.location_region,
            Job.salary_min, Job.salary_max,
        ).where(Job.id.in_(job_ids))
    ).all()
    return pd.DataFrame(rows, columns=[
        "job_id", "title", "company_name", "location_city", "location_region", "salary_min", "salary_max",
    ])


def load_job_skill_names(session: Session, job_ids: list[int]) -> pd.DataFrame:
    """One row per (job, required skill) -- real `job_skills` ground truth,
    job-side mirror of load_candidate_skill_names, used for the trending-
    skills section on the Job Skills Clustering 360 page."""
    rows = session.execute(
        select(JobSkill.job_id, Skill.name)
        .join(Skill, Skill.id == JobSkill.skill_id)
        .where(JobSkill.job_id.in_(job_ids))
    ).all()
    return pd.DataFrame(rows, columns=["job_id", "skill_name"])


def compute_weekly_creation_trend(
    session: Session, model, timestamp_column, entity_ids: list[int], num_weeks: int = 4
) -> pd.DataFrame:
    """How many of `entity_ids` were created in each of the last
    `num_weeks` non-overlapping 7-day buckets (bucket 1 = most recent) --
    "how fast is this cluster growing," using whichever real timestamp
    the entity already has (`Candidate.created_at`, `Job.posted_at`), not
    a new field. Same bucket scheme as load_weekly_event_trend
    (WEEK_BUCKET_LABELS/_ORDER) so an inflow chart and a behavioral-event
    chart read the same way. Missing weeks are real zeros, not absent
    rows -- reindexed so every week in range appears even with no
    creations in it.
    """
    weeks = list(range(1, num_weeks + 1))
    if not entity_ids:
        return pd.DataFrame({"week_bucket": weeks, "count": [0] * num_weeks})
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=num_weeks * 7)
    rows = session.execute(
        select(timestamp_column).where(
            model.id.in_(entity_ids), timestamp_column >= window_start,
        )
    ).scalars().all()
    if not rows:
        return pd.DataFrame({"week_bucket": weeks, "count": [0] * num_weeks})
    timestamps = pd.to_datetime(pd.Series(rows), utc=True)
    days_ago = (now - timestamps).dt.days
    week_bucket = (days_ago // 7 + 1).clip(upper=num_weeks)
    counts = week_bucket.value_counts().reindex(weeks, fill_value=0)
    return pd.DataFrame({"week_bucket": weeks, "count": counts.to_numpy()})


def compute_weekly_job_engagement_trend(
    session: Session, job_ids: list[int], num_weeks: int = 4
) -> pd.DataFrame:
    """How many real candidate-side behavioral events (views/clicks/
    applications/saves -- `BehavioralEvent.entity_type="job"`) referenced
    these jobs in each of the last `num_weeks` non-overlapping 7-day
    buckets. NOT `Job.posted_at`, deliberately -- checked first, and it
    turned out unusable: all 3,000 golden jobs share one recent
    generation timestamp (confirmed directly against Postgres), a
    dataset-seeding artifact, not organic posting activity; every
    cluster would show an identical single-week spike and nothing else,
    which would be presenting an artifact as a finding. Real candidate
    engagement with these jobs (received_at genuinely spread across all
    4 buckets, confirmed) is the honest trend signal for "how much
    interest is this job segment actually getting," not a new field.
    """
    weeks = list(range(1, num_weeks + 1))
    if not job_ids:
        return pd.DataFrame({"week_bucket": weeks, "count": [0] * num_weeks})
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=num_weeks * 7)
    rows = session.execute(
        select(BehavioralEvent.received_at).where(
            BehavioralEvent.entity_type == "job",
            BehavioralEvent.entity_id.in_(job_ids),
            BehavioralEvent.received_at >= window_start,
        )
    ).scalars().all()
    if not rows:
        return pd.DataFrame({"week_bucket": weeks, "count": [0] * num_weeks})
    timestamps = pd.to_datetime(pd.Series(rows), utc=True)
    days_ago = (now - timestamps).dt.days
    week_bucket = (days_ago // 7 + 1).clip(upper=num_weeks)
    counts = week_bucket.value_counts().reindex(weeks, fill_value=0)
    return pd.DataFrame({"week_bucket": weeks, "count": counts.to_numpy()})


def compute_unmet_requirement_frequency(
    skill_name_by_id: dict, matches: list, top_n: int = 10
) -> pd.DataFrame:
    """For the Job Profile 360 page: across a job's own top matching
    candidates (app.matching.rank.rank_candidates_for_job's own
    MatchResult.skill_gap -- not a second implementation), how often
    each required skill shows up as missing (confirmed absent or not
    recorded). "Even your best-matching candidates often lack X" is a
    real hiring-relevant signal straight from the matching engine, the
    reverse direction of compute_job_skill_gap (which aggregates a
    candidate's missing skills across their top matching jobs).
    """
    if not matches:
        return pd.DataFrame(columns=["skill_name", "count", "proportion"])
    counter: Counter = Counter()
    for m in matches:
        missing = m.skill_gap.confirmed_absent_required | m.skill_gap.not_recorded_required
        for skill_id in missing:
            counter[skill_name_by_id.get(skill_id, f"#{skill_id}")] += 1
    if not counter:
        return pd.DataFrame(columns=["skill_name", "count", "proportion"])
    top = counter.most_common(top_n)
    return pd.DataFrame({
        "skill_name": [name for name, _ in top], "count": [count for _, count in top],
        "proportion": [count / len(matches) for _, count in top],
    })


def compute_job_engagement_trend_vs_cluster(
    session: Session, job_id: int, cluster_job_ids: list[int], num_weeks: int = 4
) -> pd.DataFrame:
    """This one job's own weekly engagement-event count against its
    cluster's average -- same reindex-for-true-zeros reasoning as
    compute_weekly_trend_vs_cluster, just over
    compute_weekly_job_engagement_trend's single metric (entity_type=
    "job" event volume) instead of three candidate event types, since
    there's one metric here, not three.
    """
    weeks = list(range(1, num_weeks + 1))
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=num_weeks * 7)
    rows = session.execute(
        select(BehavioralEvent.entity_id, BehavioralEvent.received_at).where(
            BehavioralEvent.entity_type == "job",
            BehavioralEvent.entity_id.in_(cluster_job_ids),
            BehavioralEvent.received_at >= window_start,
        )
    ).all()
    df = pd.DataFrame(rows, columns=["job_id", "received_at"])
    if df.empty:
        counts = pd.Series(dtype="int64")
    else:
        received_at = pd.to_datetime(df["received_at"], utc=True)
        days_ago = (now - received_at).dt.days
        df["week_bucket"] = (days_ago // 7 + 1).clip(upper=num_weeks)
        counts = df.groupby(["job_id", "week_bucket"]).size()

    full_index = pd.MultiIndex.from_product([cluster_job_ids, weeks], names=["job_id", "week_bucket"])
    full = counts.reindex(full_index, fill_value=0).rename("count").reset_index()
    cluster_avg = full.groupby("week_bucket")["count"].mean().reset_index(name="cluster_avg")
    target = full.loc[full["job_id"] == job_id].rename(columns={"count": "job_count"})
    merged = target.merge(cluster_avg, on="week_bucket")
    merged["cluster_size"] = len(cluster_job_ids)
    return merged[["week_bucket", "job_count", "cluster_avg", "cluster_size"]]
