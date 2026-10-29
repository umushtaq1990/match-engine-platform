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

## Operations
- [hardware-inventory.md](hardware-inventory.md) — measured dev machine specs and sizing
  implications
- [progress.md](progress.md) — running log of what's been done, phase by phase

## Not yet created (per PRD Section 14 / brief Section 14 — created when there's enough to say)
- `architecture.md` — system context, component, data-flow, deployment diagrams
- `infrastructure-sizing.md` — CPU/RAM/storage/network sizing methodology and scenarios (Phase 9)
- `security-and-threat-model.md` (Phase 6/8)
- `evaluation-strategy.md` — ranking quality evaluation methodology (Phase 4)
- `deployment-runbook.md` (Phase 7)
- `offline-deployment.md` (Phase 10)
- `monitoring-and-alerting.md` (Phase 9)
- `backup-and-recovery.md` (Phase 9/13)
- `cost-model.md` (Phase 11, if Azure is used)
- `production-readiness.md` (Phase 13)
- `interview-preparation.md` (end of project)
- A prioritized task backlog, a known-issues list, a benchmark-results directory, and a record of
  implemented/unimplemented security controls (per brief Section 14) — created once there's
  enough real content to track, not as empty placeholders.
