# Skill-Based Candidate Clustering: Real-Time k-means + Nightly Louvain

## Purpose & Scope

Leadership wants a 360-degree view: group candidates by skillset, visualize it, assign a *new*
candidate to a cluster the instant they sign up or edit their profile, and connect that cluster
identity to how people actually behave on the platform. Two clustering approaches already existed
in isolation before this: Phase 17's per-candidate skills embedding
(`entity_embeddings`, `app/embeddings/`) was never clustered, and Phase 23's Neo4j Louvain
candidate clustering (`app/graph/cluster_entities.py`, see
[knowledge-graph.md](knowledge-graph.md)) only printed a report -- nothing persisted it or linked
it to anything else. `app/clustering/` connects the two: a real-time k-means assignment path for
new/changed candidates, and a nightly job that refits it and links each candidate's result to that
night's fresh Louvain community, plus a new drilldown dashboard page showing both alongside real
platform engagement.

**Scope**: the clustering mechanism (fit/assign/sync) runs over every candidate with a skills
embedding -- realistic production behavior, not limited to the golden dataset. The new dashboard
page visualizes the ~1000-candidate golden subset specifically, same as the other three drilldown
pages, since that's where domain/occupation/behavioral-segment ground truth already exists for a
clean demo.

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

### New tables (`app/clustering/models.py`)

- `skill_cluster_centroids`: one current row per `(embedding_model, cluster_label)` -- same
  "latest wins, no history" pattern as `EntityEmbedding` itself (`app/embeddings/models.py`), not
  an append-only log.
- `candidate_skill_clusters`: one row per candidate -- `cluster_label` (k-means, updated by every
  refit/real-time assignment) and `louvain_community_id`/`graph_synced_at` (null until the nightly
  job's graph step has run at least once; a real-time assignment never touches these).

### Fit + assign (`app/clustering/kmeans_skill_clusters.py`)

- `fit_skill_clusters(session, embedding_model, k=8)`: refits `KMeans` over every candidate's
  current skills embedding, replaces that model's centroids, reassigns everyone. `k=8` is
  measured, not assumed -- an earlier default of 4 (chosen to match the then-documented domain
  count) didn't survive an actual elbow/silhouette sweep
  (`app/golden/drilldown/data.py::compute_k_diagnostics`, now also rendered on the dashboard
  page itself): silhouette is low and fairly flat across k (weak global separation, consistent
  with this project's own candidate/job purity findings), but with a real local peak around
  k=7-8, and that peak lines up with Louvain's own, independently re-measured community count on
  this same candidate population (knowledge-graph.md: 8 communities) -- two independent methods
  landing near the same k is a real signal. 8 chosen over 7 for that direct agreement.
- `assign_cluster_for_candidate(session, candidate_id, embedding_model)`: the real-time hot path.
  `fit_skill_clusters` uses the same nearest-centroid logic in bulk (via `sklearn`'s own
  `.predict()`) -- no duplicated assignment logic between the batch and single-candidate paths.

### Nightly job (`app/clustering/nightly_job.py`)

Runs, in order: `refresh_all` (embeddings) -> `fit_skill_clusters` (k-means) -> `sync_graph.sync()`
(Postgres -> Neo4j) -> `cluster_entities.cluster("candidate")` (Louvain) -> writes `communityId`
back onto `candidate_skill_clusters`, then prints an agreement report: for each k-means cluster,
the Louvain community its members most commonly belong to, and what fraction agree -- same
"independent-signal check" framing as `cluster_entities.py`'s own
`compute_mean_occupation_purity`, not assumed. Run via `docker compose run --rm cluster-nightly`
(own image, `docker/Dockerfile.clustering` -- its dependency profile spans both the
Ollama-embeddings world and the Neo4j-driver world, neither existing image alone covers it).

### Dashboard (`app/golden/drilldown/pages/4_Skill_Clusters_360.py`)

A fourth page in the existing drilldown app: a t-SNE scatter colored by k-means cluster, a
cluster-vs-domain/occupation crosstab (does unsupervised clustering line up with known structure),
a cluster-vs-Louvain-community crosstab (the explicit link), and a cluster-vs-engagement crosstab
(segment/persona/behavioral cluster, reusing page 3's `load_behavioral_rows`/
`compute_behavioral_clusters`) -- the actual 360 view, skill identity next to real platform
behavior. Same `cascading_filters` drill-down mechanism as every other page.

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
