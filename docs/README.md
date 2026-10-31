# JME Documentation Index

All project docs live flat under `docs/` (per `docs/prd.md` Section 14 / the project brief),
plus `docs/adr/` for architecture decision records. Every subsystem doc follows a standard
template — Purpose & Scope, Architecture & Design, Implementation, Verification & Results, Known
Limitations, (Roadmap), Related Documents — so any doc can be read the same way regardless of when
it was written. `docs/progress.md` is the one exception: it's the chronological build log, kept
deliberately phase-by-phase since that's its actual job. This file is the table of contents —
update it whenever a doc is added, renamed, or removed.

## Product & requirements
- [prd.md](prd.md) — product requirements: objective, use cases A–F, scope boundaries, Definition
  of Done
- [requirements.md](requirements.md) — functional/non-functional requirements, known/unknown/
  assumed status per item
- [assumptions-register.md](assumptions-register.md) — every assumption made, why, and how it
  was/will be validated

## Architecture
- [architecture.md](architecture.md) — the consolidated, arc42-shaped overview: system context,
  solution strategy, building blocks, runtime views, deployment, quality requirements measured
  against targets, and risks — the "start here" document, linking out to every deep-dive doc rather
  than duplicating it. Anchored by two diagrams in `docs/architect/` (solution context + full
  technical architecture, both C4-style with explicit doc references on every container)
- [data-model.md](data-model.md) — schema reference, design rationale, measured dataset scale
- [adr/](adr/) — architecture decision records (one file per significant decision, numbered)
  - [0001-decouple-ingestion-migrate-api.md](adr/0001-decouple-ingestion-migrate-api.md)

## Data governance
- [data-quality.md](data-quality.md) — data-quality check design, thresholds, verified results

## Matching engine
- [matching-engine.md](matching-engine.md) — scoring formula, eligibility rules, skill-gap logic,
  API exposure, verified results and known limitations

## Evaluation
- [evaluation-strategy.md](evaluation-strategy.md) — ground-truth design, metrics, measured
  baseline results, known limitations

## Security
- [security-and-threat-model.md](security-and-threat-model.md) — API authN/authZ approach, rate
  limiting, what it does/doesn't protect against, secrets handling, known gaps

## Observability
- [observability.md](observability.md) — Prometheus+Grafana+cAdvisor stack, what's verified vs.
  a known Docker Desktop limitation, batch-time tracking

## Kubernetes
- [kubernetes.md](kubernetes.md) — local-cluster deployment (verified end-to-end), Azure-shaped
  overlay (documented, not provisioned), real bugs found and fixed

## Production readiness
- [production-readiness.md](production-readiness.md) — honest Definition-of-Done gap assessment,
  load/soak test results and the bottleneck found and fixed, what's still missing

## Operations
- [infrastructure-sizing.md](infrastructure-sizing.md) — CPU/RAM sizing methodology and scenarios:
  measured per-service memory footprint against the real full stack, the 1M/50K scale-test numbers,
  Kubernetes resource allocation, and an illustrative Azure cost estimate derived from that sizing
- [hardware-inventory.md](hardware-inventory.md) — the original raw dev-machine measurement;
  `infrastructure-sizing.md` is now the authoritative, up-to-date sizing reference
- [offline-deployment.md](offline-deployment.md) — air-gapped deployment bundle, verified
  end-to-end with zero network access
- [backup-and-recovery.md](backup-and-recovery.md) — `pg_dump`/`pg_restore` scripts, a real
  verified backup -> restore cycle (row counts and the pgvector extension/index both confirmed
  intact), known limitations (manual, no off-host storage)
- [progress.md](progress.md) — chronological build log, phase by phase — the one doc in this
  index that's intentionally organized by development history rather than by topic

## Behavioral analytics
- [behavioral-analytics.md](behavioral-analytics.md) — user engagement/event-tracking: service
  boundary, event schema, the Redis Streams-decoupled instrumentation hook, the synthetic event
  generator, and rule-based RFM engagement segmentation with a real honest finding about the
  synthetic generator's recency/frequency correlation. ML clustering remains a documented future
  step.

## Model serving
- [model-serving.md](model-serving.md) — shared LLM/embedding serving layer: service boundary,
  tool choice (Ollama), starting models, consumer pattern, verified with real inference calls

## Skill extraction
- [skill-extraction.md](skill-extraction.md) — Use Case C: hybrid LLM-extraction +
  embedding-canonicalization pipeline, golden dataset, extraction-accuracy metrics, a measured
  baseline tuned from F1 0.504 to 0.60-0.66 (`qwen2.5:1.5b` promoted as the default model), and
  basic CV upload storage (PDF-only, 2MB cap, SeaweedFS object storage)

## Embedding matching
- [embedding-matching.md](embedding-matching.md) — Use Case F: pgvector schema, multi-dimensional
  (skills/experience/overall) embeddings, the two-stage retrieve-then-score architecture for
  scoring one candidate against 100,000+ jobs, the real measured rule-based-vs-embeddings
  comparison (resolving assumption A5's deferred promise), a model comparison where the simpler
  model won, and the eventual promotion of blended ranking to the default path

## Knowledge graph
- [knowledge-graph.md](knowledge-graph.md) — Neo4j + GDS graph linking candidates/skills/
  occupations/jobs/courses: idempotent batch sync, native Louvain skill clustering, and
  candidate/job clustering — two independent runs that both recovered the same 4 skill domains
  purely from co-skill patterns, with a measured threshold choice and an honestly-flagged
  purity-granularity caveat

## Data at scale
- [data-at-scale.md](data-at-scale.md) — domain-anchored synthetic data generation (fixing the
  knowledge graph's original "no clustering signal" finding), bulk-insert generation verified at
  the real 1M-candidate/50K-job target, a real N+1 bug found and fixed in the semantic matching
  path, and every downstream baseline re-verified with fresh numbers

## Redis infrastructure
- [redis-infrastructure.md](redis-infrastructure.md) — three scoped use cases, all built and
  verified: a Redis-backed rate-limiting store (fixing a measured multi-worker counter-inflation
  bug), 1-hour cache-aside result caching (`X-Cache: HIT`/`MISS`), and Redis Streams decoupling the
  analytics instrumentation hook from a synchronous HTTP call — plus v2 hardening: version-counter
  cache invalidation triggered by real embeddings recomputation, and consumer retry/dead-letter
  handling verified against a deliberately simulated crashed consumer

## Not yet created — written just-in-time, not upfront

Per the brief's Section 14 list, but consolidated rather than one file per bullet (see rationale
below): each is written when its phase produces something real to document, not as a placeholder.

- `deployment-runbook.md` — never built as a standalone doc; `backup-and-recovery.md` was built
  once there was something real to document (see Operations above)
- `interview-preparation.md` (end of project)
- A prioritized task backlog, a known-issues list, a benchmark-results directory (raw measured
  output — CSV/JSON, not prose), and a record of implemented/unimplemented security controls —
  created once there's enough real content to track.

### Why consolidated, not one file per brief bullet
Standard practice (arc42 for architecture docs, "living documentation" principle) treats a doc
list like the brief's Section 14 as *concerns to cover*, not a mandate for one standing file per
item. A thin doc that restates another doc's content, or that exists before its phase has
produced anything real, is worse than no doc — it's something to keep in sync for no benefit.
Consolidate first; split a section into its own file only once it's grown large enough to need
one.

### Why every subsystem doc shares one template
Before this pass, each doc was organized around "Phase N — what changed," which is the right shape
for a build log but the wrong one for reference material: a reader looking for "how does rate
limiting work" had to reconstruct the answer from three separate phase sections. Every doc now
answers the same six questions in the same order, regardless of how many phases contributed to it.
Nothing was deleted in that pass — every measured number, honest limitation, and finding that used
to live under a "## Phase N" heading now lives under the template section it actually belongs to;
phase numbers remain as inline traceability detail, not as the organizing structure.
