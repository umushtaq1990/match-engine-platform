# Knowledge Graph — Linking Candidates, Skills, Occupations, Jobs, Courses

Status: v1 — **implemented and verified end-to-end**, including a real, root-caused finding about
the clustering result's strength and its later resolution (see "Verification & Results" below)
rather than a flattering but fabricated one. This document was written and reviewed before any code
existed — get the shape right on paper first, then build against it. The design matched the build
with no changes required.

## Purpose & Scope

Everything built elsewhere in this project answers "how similar are these two things" (rule-based
scoring, embedding cosine similarity). A knowledge graph answers a different class of question
entirely:

- **Multi-hop relationships**: "candidates two hops from this job, via a shared course" — this is
  an awkward chain of SQL joins and natural as a graph traversal.
- **Community detection**: "which skills cluster together," or "which candidates/jobs group into
  implicit segments" — not a comparison between two named things, a structural property of the
  whole network.
- **Centrality**: "which skills are quietly load-bearing across many occupations" — again a
  network-wide property, not a pairwise score.

None of the existing infrastructure (Postgres joins, pgvector similarity) is the right tool for
these — this is a genuinely different problem shape, not a reimplementation of something already
solved elsewhere.

In scope: schema mirroring the existing relational entities, idempotent batch sync, native GDS
Louvain clustering (both skill-level and candidate/job-level), and Neo4j Browser visualization. Out
of scope for now: a live query API, a custom visualization beyond Neo4j Browser, and feeding a
graph-based relatedness score into the matching engine (see "Roadmap").

## Architecture & Design

### Tool choice: Neo4j Community Edition, not networkx alone

Official Docker image (`neo4j:5.26.32-community`, confirmed via Docker Hub, same pattern as every
other third-party service in this project), not a custom build. Chosen over a pure-Python
in-memory graph (`networkx` alone) deliberately: the task calls for a real queryable graph service
(Cypher queries, persistence, a built-in visualization UI), not just an analysis library. This is
one of the few places in this project where the simplest possible option isn't the right call —
the problem is genuinely graph-shaped, and a real graph database is the correct tool for it, not
an over-engineered one.

**Graph Data Science (GDS) plugin: used, after actually verifying the license question instead of
guessing.** An initial check was inconclusive, but digging further and testing directly resolved
it: Neo4j's own docs state Community Edition includes *every* GDS algorithm — the Enterprise-only
features are backup/restore and Arrow import/export, neither relevant here — capped only at 4 CPU
cores (irrelevant at this project's scale). Confirmed directly by running it, not just reading
about it: installed the plugin into a disposable test container via `NEO4J_PLUGINS=["graph-data-
science"]`, and the startup log itself printed `GDS license state: unlicensed` (the documented
free/Community mode) before successfully running a real `gds.louvain.stream()` call. GDS is the
real tool for this job — native, optimized graph algorithms running inside the database engine
beat a Python round-trip through `networkx` on both performance and on what it demonstrates.

### Sync: batch ETL, not live dual-write

A one-off job (`app/graph/sync_graph.py`, the same `profiles: tools` pattern as `migrate`/`seed`/
`analytics-seed`/`embeddings-compute`) reads from Postgres via the existing ORM models and writes
into Neo4j via `MERGE` Cypher statements — idempotent by construction, so re-running the sync is
always safe and never duplicates nodes/edges. **Not** a live dual-write from the application into
two databases simultaneously — that's real distributed-consistency complexity with no payoff yet,
since nothing currently *queries* the graph at request time. Re-running a full sync is cheap at
this dataset's scale (thousands of nodes, tens of thousands of edges), so no incremental/hash-based
invalidation (unlike the embeddings pipeline) is needed — a deliberate scope difference, not an
oversight.

### Service boundary

New `app/graph/` package, own image (`docker/Dockerfile.graph`) — not reused from any existing
service, because its dependency profile is genuinely different (Postgres read access + the `neo4j`
driver; no FastAPI, no Ollama) from every other service in this project. The sync job is plain
Python; clustering itself runs server-side via GDS/Cypher, so no `networkx`/graph-algorithm
dependency is needed in this image at all. No live HTTP API — batch sync and batch clustering jobs
only; an API layer is deferred until something actually needs to query the graph at request time
(see "Roadmap").

### Clustering design: native GDS, validated against an independent signal

1. The clustering job (`app/graph/cluster_skills.py`) runs entirely in Cypher via the GDS plugin,
   not a Python round-trip: a Cypher query first derives a **skill co-occurrence relationship**
   (two skills connected, weighted by how often they co-occur on the same candidate/job/occupation/
   course, from the already-synced `HAS_SKILL`/`REQUIRES_SKILL`/`SKILL_OF_OCCUPATION`/
   `TEACHES_SKILL` edges), then `gds.graph.project` builds an in-memory GDS graph projection over
   it, and `gds.louvain.write` runs Louvain community detection and writes the resulting
   `communityId` directly back onto each `Skill` node — all server-side.
2. Neo4j Browser can then color/group skills by `communityId` directly, no extra processing step.

**Validation, avoiding a circularity mistake**: every skill in `SKILL_TAXONOMY` already has a
`category` field (programming, cloud, healthcare, soft_skill, trades, etc. — see
`app/ingestion/generate_synthetic_data.py`) that was **never used to construct the co-occurrence
graph**, making it a legitimate independent check: do the *discovered* communities roughly line up
with the *already-known* categories? Rough agreement is a sanity check that the method works at
all; disagreement would be a real, interesting finding worth investigating, not a failure to hide.

### Candidate/job clustering — the same pattern, one level up

`app/graph/cluster_entities.py` extends the identical native-GDS Louvain pattern to derive a
**candidate-candidate** (or **job-job**) similarity relationship — two candidates connected,
weighted by how many skills they share (via `HAS_SKILL`/`REQUIRES_SKILL`) — then runs the same
project/Louvain/write sequence as skill clustering. The point isn't to re-cluster skills again; it's
to ask whether *people* (or *job postings*) group into implicit segments that aren't just a
restatement of the explicit `occupation_id` label — genuine "talent segments," not derived from a
label already in the data.

**A threshold, chosen by measurement, not guessed**: edges only form between entities sharing **at
least 2** skills, not 1 (`MIN_SHARED_SKILLS`). Measured directly: at threshold 1, the
candidate-candidate graph has **808,575** edges and mean occupation purity **0.0523**; at threshold
2, **214,926** edges (~3.8x fewer) and purity **0.2891** (~5.5x higher). A single shared skill is
weak, noisy signal at this scale — pruning it both shrinks the graph and sharpens the clustering.

## Implementation

### Schema — mirrors the existing relational entities exactly, nothing invented

| Node | Backed by |
|---|---|
| `Candidate` | `candidates` |
| `Job` | `jobs` |
| `Skill` | `skills` (keeps its existing `category` field — see "Validation" above) |
| `Occupation` | `occupations` |
| `Course` | `courses` |

| Edge | Backed by | Weight/property |
|---|---|---|
| `(Candidate)-[:HAS_SKILL]->(Skill)` | `candidate_skills` | `proficiency` |
| `(Job)-[:REQUIRES_SKILL]->(Skill)` | `job_skills` | `requirement_level` |
| `(Occupation)-[:SKILL_OF_OCCUPATION]->(Skill)` | `occupation_skills` | `importance` |
| `(Course)-[:TEACHES_SKILL]->(Skill)` | `course_skills` | `coverage_weight` |
| `(Candidate)-[:TARGETS_OCCUPATION]->(Occupation)` | `candidates.primary_occupation_id` | — |
| `(Job)-[:POSTED_FOR_OCCUPATION]->(Occupation)` | `jobs.occupation_id` | — |
| `(Candidate)-[:ENGAGED_WITH]->(Job)` | `analytics.behavioral_events` | `event_count`, `last_event_at` — the one edge type that isn't a 1:1 junction-table mirror; it's an aggregation of the behavioral-events log, a deliberate cross-subsystem tie-in, not scope creep |
| `(Candidate)-[:CANDIDATE_SIMILAR_TO]->(Candidate)` | derived, not a relational table | `weight` = shared-skill count (candidate/job clustering only) |
| `(Job)-[:JOB_SIMILAR_TO]->(Job)` | derived, not a relational table | `weight` = shared-skill count (candidate/job clustering only) |

Every node/edge type maps to something that already exists (or is derived transparently from
existing data) — this subsystem links and re-exposes data, it doesn't invent new facts about
candidates/jobs/skills.

### Running it

- `docker compose run --rm graph-sync` — idempotent batch ETL from Postgres.
- `docker compose run --rm graph-cluster` — skill co-occurrence clustering, writes `communityId`
  onto `Skill` nodes.
- `docker compose run --rm graph-cluster-entities` (after `graph-sync`) — candidate/job clustering,
  writes `communityId` onto `Candidate` and `Job` nodes via `CANDIDATE_SIMILAR_TO`/
  `JOB_SIMILAR_TO`, viewable in Neo4j Browser exactly like skill communities.

### Visualization

Neo4j Browser, built into the server, reachable at `http://localhost:7474` — zero additional
frontend work needed. A custom visualization (D3/vis.js, or embedding a graph view into the
existing matching API) remains a documented future step, not needed to demonstrate the capability
now.

## Verification & Results

- **GDS licensing, resolved by testing it, not by reading about it**: installed the plugin into a
  disposable container via `NEO4J_PLUGINS`, watched the startup log print `GDS license state:
  unlicensed` (the documented free Community mode), and ran a real `gds.louvain.stream()` call
  that correctly clustered a hand-built toy graph before touching any real data.
- **Sync job**: ran for real against the full seeded dataset — 64 `Skill`, 150 `Occupation`, 100
  `Course`, 2,000 `Candidate`, 1,000 `Job` nodes; 11,814 `HAS_SKILL`, 6,034 `REQUIRES_SKILL`, 908
  `SKILL_OF_OCCUPATION`, 195 `TEACHES_SKILL`, 1,899 `TARGETS_OCCUPATION`, 1,000
  `POSTED_FOR_OCCUPATION`, and 34,788 `ENGAGED_WITH` edges (the last confirming the cross-subsystem
  tie-in actually works, not just reads plausible). **Idempotence verified by re-running**:
  identical counts the second time, confirmed directly via `cypher-shell` independently of the
  job's own printed output.
- **Neo4j Browser**: confirmed reachable (`HTTP 200` at `localhost:7474`), not just assumed to
  work because the container is "up."

### A real, root-caused finding: the clustering signal was weak, and here's exactly why

Running `cluster_skills.py` for real initially produced **4 communities, modularity 0.0451, mean
category purity 0.1585** (barely above the ~0.067 a uniform random guess would score across 15
categories) — a weak result, reported honestly rather than cherry-picked or hidden.

Investigated, not just reported: queried the derived `CO_OCCURS_WITH` relationship directly and
found **2,016 edges — exactly 64-choose-2, every possible skill pair**. The co-occurrence graph was
*complete*. Root cause, confirmed by reading the actual generator code: the synthetic data generator
originally assigned each occupation's skill profile via `rng.sample(skills, k=rng.randint(4, 8))` —
**uniformly at random across all 64 skills, with no category weighting**. Across 2,000 candidates,
1,000 jobs, and 150 occupations, every skill pair ended up co-occurring somewhere purely by chance.

Tested whether pruning weak edges would reveal hidden structure (a legitimate, non-invasive fix
tried before concluding anything): projecting only edges above the 75th-percentile weight still
collapsed 61 of 64 skills into one giant community — confirming there was no latent category signal
at *any* threshold, not just the default one.

This was a genuine property of the then-current synthetic dataset, not a bug in this subsystem's
code — the graph, sync, and GDS clustering pipeline all worked correctly, verified independently of
this result. The decision at the time was **not** to retroactively change the skill-sampling logic,
since doing so would regenerate the dataset and invalidate every downstream measured baseline that
depended on the then-current skill distribution — a much larger, riskier change than this
subsystem's own scope. The honest result stood as reported, until the user explicitly asked for
exactly this change later (see below).

### The generator was changed, deliberately and with full re-verification

Category-coherent skill sampling was introduced as part of a broader scale/realism pass (see
`docs/data-at-scale.md`): occupation skill profiles are now anchored to one of four coarser
"domains" instead of sampled uniformly at random. Re-running the clustering job afterward: **mean
category purity 0.1585 → 0.3222**, and more strikingly, **all 4 discovered communities now map 1:1
onto the 4 designed domains with zero cross-contamination** — not an approximate improvement, a
clean recovery. The co-occurrence graph is still topologically complete (every skill pair still
co-occurs at least once), but its *weight* distribution is now sharply differentiated (min 42/max
335 vs. the old min 3/max 83) — enough for weighted Louvain to find real structure without needing
a sparse graph. Every downstream baseline this change could have invalidated was re-run and
republished, not left stale — see `docs/data-at-scale.md`'s verification section for the full
before/after.

### Candidate/job clustering — the real finding

Clustering candidates and clustering jobs — two *independent* runs, over two different graphs —
both converged on exactly **4 communities**. That's not a coincidence; it's the same 4 domains the
generator anchors skill assignment to (`software_engineering`, `business_ops`, `trades_logistics`,
`people_services`), recovered purely from candidate-candidate and job-job co-skill patterns, with
no domain label ever written to the database. This is the most direct demonstration yet that the
domain-anchoring fix produces structure a graph algorithm can actually discover, not just a number
(purity) that moved in the right direction.

**Reading the purity numbers honestly — a granularity mismatch, not a clustering failure**:
candidate purity (0.2891) is noticeably higher than job purity (0.0495) against the same kind of
check (most-common `occupation_title` per community). Occupation is a much finer-grained label (150
distinct occupations) than the 4 broad domains these communities actually represent — even a
*perfectly* domain-pure job community spanning ~37 occupations evenly would cap out near
1/37 ≈ 0.027 purity under that ground truth, so 0.0495 is modestly *above* that naive floor, not
evidence of no structure (confirmed separately: modularity 0.6065, a well-separated graph). The
candidate/job purity gap itself is an open question — not chased further in this pass, a real
avenue for future work if this capability is pushed further.

## Known Limitations

- Batch sync only — the graph can be stale relative to Postgres between sync runs. Acceptable
  since nothing queries the graph live yet.
- GDS Community is capped at 4 CPU cores for parallelism — irrelevant at this project's scale, but
  a real ceiling if this graph ever grew by orders of magnitude.
- No live query API — Neo4j Browser and ad-hoc Cypher only, not an application-facing endpoint.
- The candidate/job purity gap (candidates clustering more cleanly by occupation than jobs do) is
  observed but not root-caused — a real open question, not chased further in this pass.

## Roadmap

- A graph-based relatedness score — path length or shared-neighborhood similarity between a
  candidate and a job in this graph — would plug into the existing multi-dimensional embeddings
  blend (`app/embeddings/retrieval.py`) as a **fourth signal** alongside skills/experience/overall,
  not as a separate recommendation system. This is the natural connection point for "use the graph
  for recommendations," only after it's measured to add real value — same "prove it, then wire it
  in" discipline as everywhere else in this project.
- A custom visualization beyond Neo4j Browser, if the built-in one proves limiting for a specific
  demo need.
- Investigating the candidate/job purity gap noted above.

## Related Documents

- [`docs/data-at-scale.md`](data-at-scale.md) — the domain-anchored synthetic data generator this
  subsystem's clustering signal depends on.
- [`docs/embedding-matching.md`](embedding-matching.md) — the multi-dimensional blend a future
  graph-based relatedness signal would feed into.
- [`docs/behavioral-analytics.md`](behavioral-analytics.md) — the event log the `ENGAGED_WITH` edge
  aggregates.
- [`docs/architecture.md`](architecture.md) — system-wide overview.
- [`docs/progress.md`](progress.md) — full build history.
