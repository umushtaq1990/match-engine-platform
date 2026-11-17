# Skill-Based Candidate/Job Clustering: Real-Time k-means + Nightly Louvain

## Purpose & Scope

Leadership wants a 360-degree view: group candidates (and job postings) by skillset, visualize it,
assign a *new* candidate/job to a cluster the instant they sign up/get posted or get edited, and
connect that cluster identity to how people actually behave on the platform. Two clustering
approaches already existed in isolation before this: Phase 17's per-entity skills embedding
(`entity_embeddings`, `app/embeddings/`) was never clustered, and Phase 23's Neo4j Louvain
candidate/job clustering (`app/graph/cluster_entities.py`, see
[knowledge-graph.md](knowledge-graph.md)) only printed a report -- nothing persisted it or linked
it to anything else. `app/clustering/` connects the two: a real-time k-means assignment path for
new/changed candidates or jobs, and a nightly job that refits it and links each entity's result to
that night's fresh Louvain community, plus two drilldown dashboard pages (candidates, jobs) showing
both alongside each entity type's own real-world context.

**Scope**: the clustering mechanism (fit/assign/sync) runs over every candidate/job with a skills
embedding -- realistic production behavior, not limited to the golden dataset. The dashboard pages
visualize the golden subset specifically (~1000 candidates, 3000 jobs), same as the other drilldown
pages, since that's where domain/occupation/behavioral-segment ground truth already exists for a
clean demo. Built entity-type-aware (`"candidate"` | `"job"`) from the start of the job work
(`app/clustering/kmeans_skill_clusters.py`/`nightly_job.py`), not as two parallel modules -- the
fit/assign/link logic is identical, only the table/id column differs.

## Architecture & Design

### Why two methods, not one

Louvain community detection is a *global* algorithm -- it reconsiders the whole graph's edge
structure, not just one node, so there's no cheap "just this one candidate" mode. Re-running it on
every signup/profile edit would mean recomputing the whole graph on every request. k-means'
nearest-centroid lookup, in contrast, is O(k) per point -- cheap enough to run inline. So:

- **Real-time path (k-means)**: a new signup or profile edit gets `assign_cluster_for_candidate`
  -- refresh that one candidate's skills embedding (reuses
  `app.embeddings.compute_embeddings.refresh_entity`, already hash-invalidated), then nearest
  centroid by Euclidean distance against the persisted `skill_cluster_centroids`.
- **Nightly path (Louvain)**: `app/clustering/nightly_job.py` refits the k-means centroids against
  the current embedding distribution (profiles drift over time; centroids should too), then
  re-syncs and re-clusters the Neo4j graph (reusing `app.graph.sync_graph.sync` and
  `app.graph.cluster_entities.cluster` directly -- nothing reimplemented), and writes that run's
  Louvain `communityId` back onto each candidate's k-means row. This is the explicit link between
  the two: not a forced 1:1 mapping (they're independent methods and won't always agree), just
  both values sitting on the same row, crosstabbed on the dashboard.

No real cron/scheduler exists anywhere in this repo (confirmed) -- "nightly" means "the job a real
deployment would put on a nightly schedule," same as every other `docker compose run --rm <job>`
batch script here (`golden-cvs`, `seed`, `graph-cluster-entities`, ...). Wiring an actual scheduler
is a deployment concern, not built here.

### Why t-SNE is visualization-only

t-SNE has no `.predict()` for a new point -- each run depends on the full set of points it's given,
so it can't generalize to a candidate added afterward the way a k-means centroid can. It's
recomputed fresh on the dashboard page (`app/golden/drilldown/data.py::compute_tsne_projection`)
from whatever rows are currently shown, never stored, and never part of the assignment path.

### No StandardScaler on the embedding vectors

Unlike `app/golden/drilldown/data.py`'s existing behavioral-KPI k-means (`compute_behavioral_clusters`,
raw KPIs on wildly different scales, e.g. days vs. a 0-1 rate -- standardization is the right call
there), the skills vectors are already a trained model's (`all-minilm`) semantic embedding space.
Standardizing per-dimension would distort geometry the model itself produced, so `fit_skill_clusters`
fits `KMeans` directly on the raw 384-dim vectors.

## Implementation

### Tables (`app/clustering/models.py`)

- `skill_cluster_centroids`: one current row per `(embedding_model, entity_type, cluster_label)`
  -- same "latest wins, no history" pattern as `EntityEmbedding` itself
  (`app/embeddings/models.py`), not an append-only log. `entity_type` keeps candidates' and jobs'
  centroid spaces independent, since their skill-embedding distributions (and therefore each
  one's own best `k`) are measured separately, not assumed equal.
- `candidate_skill_clusters` / `job_skill_clusters`: exact mirrors of each other (`candidate_id`
  vs. `job_id` primary key) -- `cluster_label` (k-means, updated by every refit/real-time
  assignment) and `louvain_community_id`/`graph_synced_at` (null until the nightly job's graph
  step has run at least once; a real-time assignment never touches these).

### Fit + assign (`app/clustering/kmeans_skill_clusters.py`)

Entity-type-aware (`"candidate"` | `"job"`) rather than two parallel modules -- the fit/assign
logic is identical, only `CLUSTER_MODEL_BY_ENTITY_TYPE` (which ORM model/id column to write)
differs.

- `fit_skill_clusters(session, entity_type, embedding_model, k=None)`: refits `KMeans` over every
  entity of that type's current skills embedding, replaces that `(embedding_model, entity_type)`'s
  centroids, reassigns everyone. `k` defaults to `DEFAULT_K_BY_ENTITY_TYPE`, measured per entity
  type, not assumed: candidates land at `k=8` (silhouette's own local peak at k=7-8, lining up
  with Louvain's independently-found 8 candidate communities -- see below); jobs, swept the same
  way over all 4,002 jobs' real embeddings, land at `k=8` too (silhouette 0.2422, the sweep's own
  global optimum), close to (not forced to match) Louvain's independently-found 7 job communities
  (modularity 0.6879). Two unrelated populations both landing near k=7-8 against their own
  independent Louvain counts is a real pattern, not something assumed going in.
- `assign_cluster_for_entity(session, entity_type, entity_id, embedding_model)`: the real-time hot
  path. `fit_skill_clusters` uses the same nearest-centroid logic in bulk (via `sklearn`'s own
  `.predict()`) -- no duplicated assignment logic between the batch and single-entity paths.

### Nightly job (`app/clustering/nightly_job.py`)

Runs, in order: `refresh_all` (embeddings, both entity types) -> `sync_graph.sync()` (Postgres ->
Neo4j, both) -> for each entity type: `fit_skill_clusters` (k-means) -> `cluster_entities.cluster
(entity_type)` (Louvain, already built, one level up for jobs since Phase 23) -> writes
`communityId` back onto that type's cluster table, then prints an agreement report: for each
k-means cluster, the Louvain community its members most commonly belong to, and what fraction
agree -- same "independent-signal check" framing as `cluster_entities.py`'s own
`compute_mean_occupation_purity`, not assumed (candidates: 76-94% per cluster; jobs: 91.6-100%,
even cleaner). Run via `docker compose run --rm cluster-nightly` (own image,
`docker/Dockerfile.clustering` -- its dependency profile spans both the Ollama-embeddings world
and the Neo4j-driver world, neither existing image alone covers it).

**A real bug this surfaced, fixed, not routed around**: the first full job-embedding run crashed
deterministically (not flaky -- same failure, every retry) on `all-minilm`'s hard 256-token serving
limit. Root cause, confirmed directly from Ollama's own rejection ("input (268 tokens) is too
large to process"): `app/embeddings/text_synthesis.py::overall_text` returned a job's full
`description_raw` unbounded -- fine for candidates (always a short synthesized sentence) but
golden jobs' real LLM-written, multi-paragraph postings routinely exceed it. Fixed by truncating a
job's `overall_text` to `MAX_JOB_OVERALL_TEXT_CHARS = 800` characters (comfortably under 256
tokens even for dense text) -- an embedding needs the semantic gist, not a verbatim reproduction.

### Dashboards

Two mirrored drilldown pages, same visual language, reusing every generic chart/data helper
(`render_k_diagnostic_chart`, `render_composition_bar_chart`, `render_count_heatmap`,
`render_top_skills_bar_chart`, `render_population_bar_chart`, `compute_top_skills`,
`compute_k_diagnostics`, `name_clusters_by_majority`, `generate_skill_wordcloud`) unchanged:

- `app/golden/drilldown/pages/4_Candidate_Skills_Clustering_360.py`: t-SNE scatter colored by
  k-means cluster, cluster-vs-domain/occupation crosstab, cluster-vs-Louvain-community crosstab,
  and a cluster-vs-engagement crosstab (segment/persona/behavioral cluster, reusing page 3's
  `load_behavioral_rows`/`compute_behavioral_clusters`) -- skill identity next to real platform
  behavior, since candidates have behavioral events.
- `app/golden/drilldown/pages/6_Job_Skills_Clustering_360.py`: the same t-SNE/crosstab/Louvain
  structure over required-skill embeddings instead -- no engagement crosstab or behavioral-KPI
  diagnostic, since jobs don't log in or apply; that part of page 4 is dropped here, not faked.

Same `cascading_filters` drill-down mechanism as every other page.

## Verification & Results

- `fit_skill_clusters`/`assign_cluster_for_candidate` verified against the full population (real
  `all-minilm` embeddings for all 2,001 candidates, via the local `ollama` service, not mocked).
  `fit_skill_clusters(k=8)`: cluster sizes 186/187/510/251/176/159/196/336, a ~4.4s fit over all
  2,001 vectors. A real-time `assign_cluster_for_candidate` call correctly re-embeds and returns
  a centroid-nearest label, and the persisted row's `louvain_community_id` stays untouched by a
  k-means-only call (verified directly against Postgres).
- The full pipeline (`sync_graph.sync` -> `cluster_entities.cluster("candidate")` -> link-back) run
  for real against the full graph (2,001 `Candidate` / 4,002 `Job` nodes, 173,910 similarity edges;
  `sync_graph.sync` 20.0s, Louvain 24.4s, link-back 0.1s) found **8 Louvain communities** at
  **modularity 0.5447**, linked back onto all 2,001 candidates, with per-cluster agreement (each
  k-means cluster's most common Louvain community) ranging **78.6%-93.8%** at k=8 -- a real,
  measured, non-degenerate signal, markedly higher than the 55.6%-87.5% an earlier (wrong) k=4 fit
  gave on the same data.
- The k selection itself was checked, not assumed: an elbow/silhouette sweep (k=2..12) over the
  real embeddings found silhouette low and fairly flat (0.12-0.16) with a local peak at k=7-8,
  not at the originally-assumed k=4 -- caught directly from the dashboard's own t-SNE plot
  visually showing more separated regions than the k=4 coloring implied, then confirmed
  numerically. `DEFAULT_K` was corrected from 4 to 8 as a direct result.
- The new drilldown page verified headlessly via Streamlit's own `AppTest` utility (executes the
  page script and surfaces exceptions without a browser) against the same real data: no exceptions,
  the elbow/silhouette charts, all three crosstabs, and the t-SNE scatter all populate with real,
  non-degenerate values.
- `python -m ruff check .` and `python -m pytest tests/ -q` pass (one pre-existing, unrelated
  failure confirmed via `git stash` to predate this work -- a local test-environment DNS limitation
  hitting an `analytics` hostname that only resolves inside the Docker network).
- Jobs, mirroring the above once the text-truncation fix unblocked real job embeddings: full
  population `fit_skill_clusters("job", k=8)` over all 4,002 jobs -> cluster sizes 701/508/483/
  509/403/420/620/358. Full `cluster_entities.cluster("job")` + link-back found **7 Louvain
  communities** at **modularity 0.6879**, linked onto all 4,002 jobs, with per-cluster agreement
  **91.6%-100%** -- cleaner than candidates' own 79-94%. The elbow/silhouette sweep over real job
  embeddings found its own global optimum at k=8 (silhouette 0.2422), not assumed from candidates'
  k. `app/golden/drilldown/pages/6_Job_Skills_Clustering_360.py` verified via `AppTest` across the
  default state, the color-by toggle, and the trending-skills dropdown -- zero exceptions.

## Known Limitations

- Silhouette scores are low across every k tested (0.12-0.16) -- real structure exists (Louvain
  and k-means agree 79-94% per cluster at k=8), but it's not a tightly-separated clustering
  problem; don't over-read small differences between adjacent k values as meaningful.
- `k=8` is still a choice among several locally-plausible values (k=7 scored almost identically),
  picked for its direct agreement with Louvain's independently-found community count, not a
  single unambiguous optimum -- the dashboard's own elbow/silhouette charts make that visible
  rather than hiding it behind a fixed default.
- Same batch-sync staleness limitation as the underlying graph (knowledge-graph.md): the dashboard
  reflects whenever `cluster-nightly` last ran, not the current instant.
- Cluster-vs-cluster linking (`link_clusters_by_domain`) is by shared majority domain, not a
  direct similarity measure between the two independent embedding spaces -- confirmed clean 1:1
  across all 8 clusters on both sides for this dataset, but that's a property of this data's
  domain structure, not a guarantee the method holds at a different scale/distribution.
- `Job.posted_at` is unusable as a trend signal for this dataset: all 3,000 golden jobs share one
  recent generation timestamp (confirmed directly against Postgres -- a dataset-seeding artifact,
  not organic posting activity). The per-cluster trend charts use real candidate-engagement event
  volume (`analytics.behavioral_events`, `entity_type="job"`) instead, which does have genuine
  week-to-week variation -- but that's a proxy for "interest in this job segment," not literally
  "jobs being posted."

## Roadmap

- Re-sweep `k` (and re-check Louvain's community count) periodically as the candidate population
  grows or drifts -- today's k=8 is a measurement of the current dataset, not a permanent constant.
- Wire `assign_cluster_for_candidate` into the actual profile-update/signup path (currently a
  library function, not yet called from any API route) once there's a concrete consumer for "which
  cluster is this candidate in, right now."

## Related Documents

- [`docs/architecture/data-platform/knowledge-graph.md`](knowledge-graph.md): the Louvain
  candidate clustering this work persists and links against.
- [`docs/architecture/data-platform/embedding-matching.md`](embedding-matching.md): the Phase 17
  embedding infrastructure (`entity_embeddings`) this clustering is built directly on top of.
- [`docs/architecture/application-services/behavioral-analytics.md`](../application-services/behavioral-analytics.md):
  the segment/persona data the new dashboard page crosstabs skill clusters against.
