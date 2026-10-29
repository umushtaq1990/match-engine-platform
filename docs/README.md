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
  verified results, known limitations (Phase 3 MVP)

## Operations
- [hardware-inventory.md](hardware-inventory.md) — measured dev machine specs and sizing
  implications
- [progress.md](progress.md) — running log of what's been done, phase by phase

## Not yet created — written just-in-time, not upfront

Per the brief's Section 14 list, but consolidated rather than one file per bullet (see rationale
below): each is written when its phase produces something real to document, not as a placeholder.

- `architecture.md` — one arc42-style document (system context, building blocks, runtime,
  deployment views, quality requirements) — **not** a folder of separate diagram files
- `infrastructure-sizing.md` — CPU/RAM/storage/network sizing methodology and scenarios (Phase 9).
  Absorbs `hardware-inventory.md` as its measured baseline input, and a cost section absorbs what
  would otherwise be a separate `cost-model.md` (cost is derived directly from sizing)
- `security-and-threat-model.md` (Phase 6/8)
- `evaluation-strategy.md` — ranking quality evaluation methodology (Phase 4)
- `deployment-runbook.md` (Phase 7), `offline-deployment.md` (Phase 10),
  `monitoring-and-alerting.md` (Phase 9), `backup-and-recovery.md` (Phase 9/13) — merged into
  `production-readiness.md` instead of kept standalone if any of them end up thin
- `production-readiness.md` (Phase 13)
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
