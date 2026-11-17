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
from app.clustering.models import CandidateSkillCluster
from app.embeddings.models import EntityEmbedding
from app.extraction.models import ExtractionExampleResult, ExtractionRun
from app.golden.generate_persona_events import assign_personas
from app.golden.models import GoldenInjectedNoise
from app.golden.select import select_existing_golden_candidate_ids
from app.ingestion.source_models import Candidate, CandidateSkill, Job, Occupation, Skill

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


@st.cache_data(show_spinner="Fitting k-means across a range of k for the elbow/silhouette diagnostic...")
def compute_k_diagnostics(vectors, k_range=range(2, 13)) -> pd.DataFrame:
    """Elbow (inertia) + silhouette score per candidate k -- the actual
    justification for app/clustering/kmeans_skill_clusters.py's DEFAULT_K,
    measured here rather than assumed. Deliberately lives in this
    read-only dashboard module, not in kmeans_skill_clusters.py itself:
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


def find_similar_candidates(rows: pd.DataFrame, target_candidate_id: int, top_n: int = 10) -> pd.DataFrame:
    """Nearest neighbors of `target_candidate_id` by Euclidean distance
    over the same skills-embedding vectors load_skill_cluster_rows already
    returns -- a genuinely new capability (no candidate-to-candidate
    embedding similarity exists anywhere else in this repo;
    app/graph/cluster_entities.py's CANDIDATE_SIMILAR_TO is shared-
    skill-count based, graph-side only, never queried from here). Instant
    rather than a fresh query, since `rows` is whatever's already loaded
    for the page (the same vectors driving the t-SNE plot on page 4).
    Returns same_skill_cluster/same_louvain_community alongside the raw
    distance, so a reader can cross-check this embedding-based notion of
    "similar" against the graph-based one, same two-method framing as the
    rest of this feature.
    """
    import numpy as np

    target_row = rows.loc[rows["candidate_id"] == target_candidate_id]
    if target_row.empty:
        return pd.DataFrame(columns=[
            "candidate_id", "distance", "same_skill_cluster", "same_louvain_community",
        ])
    target = target_row.iloc[0]
    target_vector = np.array(target["vector"], dtype=np.float64)

    others = rows.loc[rows["candidate_id"] != target_candidate_id].copy()
    others["distance"] = others["vector"].apply(
        lambda v: float(np.linalg.norm(np.array(v, dtype=np.float64) - target_vector))
    )
    others["same_skill_cluster"] = others["cluster_label"] == target["cluster_label"]
    others["same_louvain_community"] = others["louvain_community_id"] == target["louvain_community_id"]
    return others.sort_values("distance").head(top_n)[[
        "candidate_id", "distance", "same_skill_cluster", "same_louvain_community",
    ]].reset_index(drop=True)


def compute_peer_skill_gap(
    target_skills: set, peer_skill_rows: pd.DataFrame, peer_count: int, min_peer_proportion: float = 0.3
) -> pd.DataFrame:
    """Skills at least `min_peer_proportion` of the target's similar
    candidates have that the target doesn't -- the "gap vs. neighbors"
    half of the Candidate Profile 360 page's skill-gap section (the other
    half, vs. matching jobs, comes straight from the real matching
    engine's own SkillGapReport, not duplicated here)."""
    if peer_skill_rows.empty or peer_count == 0:
        return pd.DataFrame(columns=["skill_name", "peer_count", "peer_proportion"])
    counts = peer_skill_rows["skill_name"].value_counts()
    counts = counts[~counts.index.isin(target_skills)]
    proportions = counts / peer_count
    gap = proportions[proportions >= min_peer_proportion]
    return pd.DataFrame({
        "skill_name": gap.index, "peer_count": counts.loc[gap.index].to_numpy(),
        "peer_proportion": gap.to_numpy(),
    }).sort_values("peer_proportion", ascending=False).reset_index(drop=True)
