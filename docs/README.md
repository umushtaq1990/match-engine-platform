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
- [progress.md](progress.md) — running log of what's been done, phase by phase

## Behavioral analytics
- [behavioral-analytics.md](behavioral-analytics.md) — user engagement/event-tracking: service
  boundary, event schema, instrumentation hook in the matching API, synthetic event generator, all
  verified end-to-end (Phase 14, v1). Rule-based segmentation and ML clustering (v2/v3) remain
  design-only, not yet built.

## Model serving
- [model-serving.md](model-serving.md) — shared LLM/embedding serving layer design: service
  boundary, tool choice (Ollama), starting models, consumer pattern, what it unblocks (Use Cases
  C/F) (Phase 15, design only — not yet built)

## Not yet created — written just-in-time, not upfront

Per the brief's Section 14 list, but consolidated rather than one file per bullet (see rationale
below): each is written when its phase produces something real to document, not as a placeholder.

- `architecture.md` — one arc42-style document (system context, building blocks, runtime,
  deployment views, quality requirements) — **not** a folder of separate diagram files
- `infrastructure-sizing.md` — CPU/RAM/storage/network sizing methodology and scenarios (Phase 9).
  Absorbs `hardware-inventory.md` as its measured baseline input, and a cost section absorbs what
  would otherwise be a separate `cost-model.md` (cost is derived directly from sizing)
- `deployment-runbook.md` (Phase 7), `backup-and-recovery.md` — never built as standalone docs;
  backup/restore is recorded as an open gap in `production-readiness.md` instead
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
