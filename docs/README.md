# JME Documentation Index

Docs are organized by architecture layer under `docs/architecture/`, by operational
concern under `docs/operations/`, plus `docs/adr/` for architecture decision records
and a handful of project-wide docs at the top level. Every subsystem doc follows a
standard template; Purpose & Scope, Architecture & Design, Implementation,
Verification & Results, Known Limitations, (Roadmap). Related Documents
can be read the same way regardless of when it was written. `docs/progress.md` is the
one exception: it's the chronological build log, kept deliberately phase-by-phase
since that's its actual job — kept locally as an internal working log, not included in
this repository. This file is the table of contents, update it whenever a doc is
added, renamed, or removed.

## Product & requirements
- [prd.md](prd.md) : product requirements: objective, use cases A–F, scope boundaries, Definition
  of Done
- `requirements.md` : functional/non-functional requirements, known/unknown/assumed status per
  item — kept locally, not included in this repository (its content is already covered by `prd.md`
  and `assumptions-register.md`)
- [assumptions-register.md](assumptions-register.md) : every assumption made, why, and how it
  was/will be validated

## Architecture Overview
- [architecture.md](architecture.md) : the consolidated, arc42-shaped overview, system context,
  solution strategy, building blocks, runtime views, deployment, quality requirements measured
  against targets, and risks, the "start here" document, linking out to every layer doc below
  rather than duplicating it. Anchored by two diagrams in `docs/architect/` (solution context +
  full technical architecture, both C4-style with explicit doc references on every container)
- [adr/](adr/) : architecture decision records (one file per significant decision, numbered)
  - [0001-decouple-ingestion-migrate-api.md](adr/0001-decouple-ingestion-migrate-api.md)
  - [0002-build-the-ingress-layer.md](adr/0002-build-the-ingress-layer.md)

## Architecture layers (`docs/architecture/`)

Each layer below is a Level-3 (C4 Component) view: its own doc, its own diagram cropped from the
master Technical Architecture diagram, and where the layer has enough real internal complexity, its own Level-4 per-container deep-dive docs underneath it.

- [architecture/ingress-and-identity.md](architecture/ingress-and-identity.md) : real Load
  Balancer/Reverse Proxy/API Gateway (nginx) plus Keycloak SSO. How a request, and the person
  behind it, gets into the system
- [architecture/frontend.md](architecture/frontend.md) : the Streamlit demo UI (Candidate
  upload-CV/view-matches, Employer view-matches), a single-container layer
- [architecture/application-services/](architecture/application-services/) : the three services carrying this platform's business logic
  - [application-services/matching-engine.md](architecture/application-services/matching-engine.md) : scoring formula, eligibility rules, skill-gap logic, API exposure
  - [application-services/behavioral-analytics.md](architecture/application-services/behavioral-analytics.md) : event ingestion, Streams consumer, RFM segmentation
  - [application-services/skill-extraction.md](architecture/application-services/skill-extraction.md) : hybrid LLM extraction + embedding canonicalization, CV upload storage
- [architecture/ai-ml-and-mlops.md](architecture/ai-ml-and-mlops.md) : shared Ollama model serving (LLM + embeddings) and the evaluation harness (MLflow + Postgres) that measures whether any model-based change actually helped
- [architecture/data-platform/](architecture/data-platform/) : every data store, plus embeddings and data-quality concerns
  - [data-platform/data-model.md](architecture/data-platform/data-model.md) : schema reference, design rationale, measured dataset scale
  - [data-platform/embedding-matching.md](architecture/data-platform/embedding-matching.md) : pgvector schema, multi-dimensional embeddings, two-stage retrieve-then-score
  - [data-platform/knowledge-graph.md](architecture/data-platform/knowledge-graph.md) : Neo4j + GDS graph, skill/candidate/job clustering
  - [data-platform/redis-infrastructure.md](architecture/data-platform/redis-infrastructure.md) : rate limiting, caching, Streams
  - [data-platform/data-quality.md](architecture/data-platform/data-quality.md) : automated post-seed data checks
  - [data-platform/data-at-scale.md](architecture/data-platform/data-at-scale.md) : domain-anchored synthetic data generation, 1M-candidate/50K-job stress testing
- [architecture/observability-and-logging.md](architecture/observability-and-logging.md) : Prometheus+cAdvisor+Grafana metrics, plus Loki+Promtail centralized structured logging

## Security
- [security-and-threat-model.md](security-and-threat-model.md) : API authN/authZ approach, rate limiting, what it does/doesn't protect against, secrets handling, known gaps

## Operations (`docs/operations/`)
- [operations/kubernetes.md](operations/kubernetes.md) : local-cluster deployment (verified end-to-end), Azure-shaped overlay (documented, not provisioned), real bugs found and fixed
- [operations/production-readiness.md](operations/production-readiness.md) : Definition-of-Done gap assessment, load/soak test results and the bottleneck found and fixed, what's still missing
- [operations/infrastructure-sizing.md](operations/infrastructure-sizing.md) : CPU/RAM sizing methodology and scenarios: measured per-service memory footprint against the real full stack, the 1M/50K scale-test numbers, Kubernetes resource allocation, an illustrative Azure cost estimate, and the original raw hardware-inventory capture (kept as an appendix)
- [operations/capacity-planning.md](operations/capacity-planning.md) : the concurrent-user scaling story: a worked scenario across the full request chain, the real bottleneck at each hop, and a real measured concurrent-load test of the LLM-backed extraction endpoint (the one load test this project hadn't run yet)
- [operations/offline-deployment.md](operations/offline-deployment.md) : air-gapped deployment bundle, verified end-to-end with zero network access
- [operations/backup-and-recovery.md](operations/backup-and-recovery.md) : `pg_dump`/`pg_restore` scripts, a real verified backup -> restore cycle (row counts and the pgvector extension/index both confirmed intact), known limitations (manual, no off-host storage)

## Build history
- `progress.md` : chronological build log, phase by phase — kept locally as an internal working
  log, not included in this repository
