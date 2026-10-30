# Knowledge Graph — Linking Candidates, Skills, Occupations, Jobs, Courses (Phase 19, design)

Status: v0.1 — **design only, not yet built**. Same discipline as Phases 12/14/15/16/17: written
and reviewed before any code exists.

## Why a graph database, not more SQL or more embeddings

Everything built so far answers "how similar are these two things" (rule-based scoring, embedding
cosine similarity). A knowledge graph answers a different class of question entirely:

- **Multi-hop relationships**: "candidates two hops from this job, via a shared course" — this is
  an awkward chain of SQL joins and natural as a graph traversal.
- **Community detection**: "which skills cluster together" — not a comparison between two named
  things, a structural property of the whole network.
- **Centrality**: "which skills are quietly load-bearing across many occupations" — again a
  network-wide property, not a pairwise score.

None of the existing infrastructure (Postgres joins, pgvector similarity) is the right tool for
these — this is a genuinely different problem shape, not a reimplementation of what Phases 4–17
already solved.

## Tool choice: Neo4j Community Edition, not networkx alone

Official Docker image (`neo4j:5.26.32-community`, confirmed via Docker Hub, same pattern as every
other third-party service in this project), not a custom build. Chosen over a pure-Python
in-memory graph (`networkx` alone) deliberately: the task calls for a real queryable graph service
(Cypher queries, persistence, a built-in visualization UI), not just an analysis library. This is
one of the few places in this project where the simplest possible option isn't the right call —
the problem is genuinely graph-shaped, and a real graph database is the correct tool for it, not
an over-engineered one.

**Note on Neo4j's Graph Data Science (GDS) plugin**: deliberately **not used**. GDS's licensing
between Community and Enterprise tiers for specific algorithms was unclear even after checking
directly — rather than depend on something ambiguous, community detection runs in Python
(`networkx` + `python-louvain`, both unambiguously open source) over data read from Neo4j via its
official driver, with results written back as a node property. This sidesteps the licensing
question entirely rather than resolving it by assumption.

## Schema — mirrors the existing relational entities exactly, nothing invented

| Node | Backed by |
|---|---|
| `Candidate` | `candidates` |
| `Job` | `jobs` |
| `Skill` | `skills` (keeps its existing `category` field — see "Validating clusters" below) |
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
| `(Candidate)-[:ENGAGED_WITH]->(Job)` | `analytics.behavioral_events` | `event_count`, `last_event_at` — the one edge type that isn't a 1:1 junction-table mirror; it's an aggregation of Phase 14's event log, a deliberate cross-phase tie-in, not scope creep |

Every node/edge type maps to something that already exists — this phase links and re-exposes data,
it doesn't invent new facts about candidates/jobs/skills.

## Sync: batch ETL, not live dual-write

A one-off job (`app/graph/sync_graph.py`, same `profiles: tools` pattern as `migrate`/`seed`/
`analytics-seed`/`embeddings-compute`) reads from Postgres via the existing ORM models and writes
into Neo4j via `MERGE` Cypher statements — idempotent by construction, so re-running the sync is
always safe and never duplicates nodes/edges. **Not** a live dual-write from the application into
two databases simultaneously — that's real distributed-consistency complexity with no payoff yet,
since nothing currently *queries* the graph at request time. Re-running a full sync is cheap at
this dataset's scale (thousands of nodes, tens of thousands of edges), so no incremental/hash-based
invalidation (unlike Phase 17's embeddings) is needed for v1 — a deliberate scope difference, not
an oversight.

## Service boundary

New `app/graph/` package, own image (`docker/Dockerfile.graph`) — not reused from any existing
service, because its dependency profile is genuinely different (Postgres read access + the `neo4j`
driver + `networkx`/`python-louvain`; no FastAPI, no Ollama) from every other service in this
project. No live HTTP API in v1 — batch sync and batch clustering jobs only; an API layer is
deferred until something actually needs to query the graph at request time (v3, recommendations).

## Clustering: skill co-occurrence, validated against an independent signal

1. The clustering job (`app/graph/cluster_skills.py`) reads the already-synced `HAS_SKILL`/
   `REQUIRES_SKILL`/`SKILL_OF_OCCUPATION`/`TEACHES_SKILL` edges back out of Neo4j and builds an
   in-memory **skill co-occurrence graph** in `networkx`: two skills are connected, weighted by how
   often they co-occur on the same candidate/job/occupation/course.
2. Runs Louvain community detection (`python-louvain`) over that co-occurrence graph.
3. Writes the resulting `community_id` back onto each `Skill` node in Neo4j — so Neo4j Browser can
   color/group skills by discovered cluster directly.

**Validation, avoiding a circularity mistake similar to the one Phase 4 was careful about**: every
skill in `SKILL_TAXONOMY` already has a `category` field (programming, cloud, healthcare,
soft_skill, trades, etc. — see `app/ingestion/generate_synthetic_data.py`) that was **never used to
construct the co-occurrence graph**, making it a legitimate independent check: do the
*discovered* communities roughly line up with the *already-known* categories? Rough agreement is a
sanity check that the method works at all; disagreement would be a real, interesting finding worth
investigating, not a failure to hide.

## Visualization

Neo4j Browser, built into the server, reachable at `http://localhost:7474` — zero additional
frontend work for v1. A custom visualization (D3/vis.js, or embedding a graph view into the
existing matching API) is a documented future step (v2), not needed to demonstrate the capability
now.

## What this unblocks (not built in this phase)

A graph-based relatedness score — path length or shared-neighborhood similarity between a
candidate and a job in this graph — would plug into Phase 17's existing multi-dimensional blend
(`app/embeddings/retrieval.py`) as a **fourth signal** alongside skills/experience/overall, not as
a separate recommendation system. This is the natural connection point for "later we can use it
for recommendations," explicitly deferred to v3.

## Phased roadmap

- **v1 (this phase)**: schema, idempotent batch sync from Postgres, skill co-occurrence clustering
  (Louvain, validated against existing skill categories), Neo4j Browser visualization.
- **v2**: candidate/job clustering (not just skills), a custom visualization beyond Neo4j Browser
  if the built-in one proves limiting for a specific demo need.
- **v3**: a graph-based relatedness signal feeding into Phase 17's multi-dimensional matching blend
  — only after v1/v2 produce something concrete enough to measure, same "prove it, then wire it
  in" discipline as every other phase.

## Known limitations (v1, by design)

- Batch sync only — the graph can be stale relative to Postgres between sync runs. Acceptable
  since nothing queries the graph live yet.
- GDS plugin not used (licensing ambiguity) — Louvain via `networkx`/`python-louvain` instead,
  which is fine at this dataset's scale but wouldn't scale to a much larger graph the way GDS's
  native implementation would.
- No live query API — Neo4j Browser and ad-hoc Cypher only for v1, not an application-facing
  endpoint.
- Clustering scope is skills only in v1 — candidate/job-level clustering is v2, not built now.
