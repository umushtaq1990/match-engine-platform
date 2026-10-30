# JME Documentation Index

All project docs live flat under `docs/` (per `docs/prd.md` Section 14 / the project brief),
plus `docs/adr/` for architecture decision records. This file is the table of contents —
update it whenever a doc is added, renamed, or removed.

## Product & requirements
- [prd.md](prd.md) — product requirements: objective, use cases A–F, scope boundaries, Definition
  of Done
- [requirements.md](requirements.md) — functional/non-functional requirements, known/unknown/
  assumed status per item
- [assumptions-register.md](assumptions-register.md) — every assumption made, why, and how it
  was/will be validated

## Architecture
- [data-model.md](data-model.md) — schema reference, design rationale, measured dataset scale
- [adr/](adr/) — architecture decision records (one file per significant decision, numbered)
  - [0001-decouple-ingestion-migrate-api.md](adr/0001-decouple-ingestion-migrate-api.md)

## Data governance
- [data-quality.md](data-quality.md) — data-quality check design, thresholds, verified results

## Matching engine
- [matching-engine.md](matching-engine.md) — scoring formula, eligibility rules, skill-gap logic,
  verified results, known limitations (Phase 3 MVP), API exposure (Phase 6)

## Evaluation
- [evaluation-strategy.md](evaluation-strategy.md) — ground-truth design, metrics, measured
  baseline results, known limitations (Phase 4)

## Security
- [security-and-threat-model.md](security-and-threat-model.md) — API authN/authZ approach, what
  it does/doesn't protect against, secrets handling, known gaps (Phase 6)

## Observability
- [observability.md](observability.md) — Prometheus+Grafana+cAdvisor stack, what's verified vs.
  a known Docker Desktop limitation, batch-time tracking (Phase 9)

## Kubernetes
- [kubernetes.md](kubernetes.md) — local-cluster deployment (verified end-to-end), Azure-shaped
  overlay (documented, not provisioned), real bugs found and fixed (Phase 12)

## Production readiness
- [production-readiness.md](production-readiness.md) — honest Definition-of-Done gap assessment,
  load test results and the bottleneck found and fixed, what's still missing (Phase 13)

## Operations
- [hardware-inventory.md](hardware-inventory.md) — measured dev machine specs and sizing
  implications
- [offline-deployment.md](offline-deployment.md) — air-gapped deployment bundle, verified
  end-to-end with zero network access (Phase 10)
- [backup-and-recovery.md](backup-and-recovery.md) — `pg_dump`/`pg_restore` scripts, a real
  verified backup -> restore cycle (row counts and the pgvector extension/index both confirmed
  intact), known limitations (manual, no off-host storage)
- [progress.md](progress.md) — running log of what's been done, phase by phase

## Behavioral analytics
- [behavioral-analytics.md](behavioral-analytics.md) — user engagement/event-tracking: service
  boundary, event schema, instrumentation hook in the matching API, synthetic event generator, all
  verified end-to-end (Phase 14, v1). Rule-based segmentation and ML clustering (v2/v3) remain
  design-only, not yet built.

## Model serving
- [model-serving.md](model-serving.md) — shared LLM/embedding serving layer: service boundary,
  tool choice (Ollama), starting models, consumer pattern, what it unblocks (Use Cases C/F), all
  verified with real inference calls (Phase 15, v1). No consumers wired up yet.

## Skill extraction
- [skill-extraction.md](skill-extraction.md) — Use Case C: hybrid LLM-extraction +
  embedding-canonicalization pipeline, golden dataset, extraction-accuracy metrics, a real baseline
  measured and tuned (F1 0.504 -> 0.60-0.66 after a real threshold sweep + LLM comparison,
  `qwen2.5:1.5b` promoted as the new default) (Phase 16, tuned Phase 18, v2).

## Embedding matching
- [embedding-matching.md](embedding-matching.md) — Use Case F groundwork: pgvector schema,
  multi-dimensional (skills/experience/overall) embeddings, the two-stage retrieve-then-score
  architecture for scoring one candidate against 100,000+ jobs, invalidation, the real measured
  rule-based-vs-embeddings comparison (resolving assumption A5's deferred promise since Phase 4),
  and a real embedding-model comparison where the simpler model won (Phase 17, tuned Phase 18, v2).

## Knowledge graph
- [knowledge-graph.md](knowledge-graph.md) — Neo4j + GDS graph linking candidates/skills/
  occupations/jobs/courses, idempotent batch sync verified against the full seeded dataset, native
  Louvain clustering, Neo4j Browser visualization, and a real root-caused finding about why the
  clustering signal is weak on the current synthetic data (Phase 19, v1)

## Data at scale
- [data-at-scale.md](data-at-scale.md) — category-coherent skill sampling (fixing Phase 19's
  "no clustering signal" finding), bulk-insert generation, a 1M-candidate/50K-job stress-test
  invocation, and the honest boundary between what scales locally (Postgres/Neo4j/matching) and
  what doesn't (local LLM embeddings) (Phase 20, design only — not yet built)

## Redis infrastructure
- [redis-infrastructure.md](redis-infrastructure.md) — three scoped use cases: a Redis-backed
  rate-limiting store (fixing Phase 18's measured multi-worker bug), 1-hour result caching, and
  Redis Streams for async decoupling of the analytics instrumentation hook (Phase 21, design only
  — not yet built)

## Not yet created — written just-in-time, not upfront

Per the brief's Section 14 list, but consolidated rather than one file per bullet (see rationale
below): each is written when its phase produces something real to document, not as a placeholder.

- `architecture.md` — one arc42-style document (system context, building blocks, runtime,
  deployment views, quality requirements) — **not** a folder of separate diagram files
- `infrastructure-sizing.md` — CPU/RAM/storage/network sizing methodology and scenarios (Phase 9).
  Absorbs `hardware-inventory.md` as its measured baseline input, and a cost section absorbs what
  would otherwise be a separate `cost-model.md` (cost is derived directly from sizing)
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
