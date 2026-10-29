# Requirements — JME Portfolio Platform

Status: v0.2 — all 13 phases complete as of 2026-10-29; updated to reflect what's actually built
vs. still deferred (see `docs/production-readiness.md` for the full honest gap assessment).

## Functional requirements
See `docs/prd.md` Section 5 (Use Cases A–F) for the authoritative list. Summary (✅ built and
verified, 🟡 explicitly deferred — not forgotten, see `docs/progress.md`):
- ✅ Candidate-to-job and job-to-candidate ranked matching with explanations (Use Cases A/B)
- ✅ Skill-gap analysis distinguishing "not recorded" vs "assessed and absent" (Use Case D)
- ✅ Course/learning recommendations against identified gaps (Use Case E)
- 🟡 Free-text skill extraction into a canonical taxonomy (Use Case C) — deferred per assumption
  A5b, to be built NLP/embedding-first when resumed, not rule-based
- 🟡 Similarity/semantic search (jobs, candidates, courses) and fuzzy title/skill matching
  (Use Case F) — never scheduled

## Expected users
- Internal advisor/case-worker persona reviewing recommendations (primary)
- Platform operator/engineer persona deploying and monitoring the system (secondary)
- No public/self-service users in v1 (see PRD Section 6, scope boundaries)

## Synthetic dataset size
**Resolved (Phase 2)**: 2,000 candidates, 1,000 jobs, 150 occupations, 100 courses, 64 curated
skills — see `docs/data-model.md` for the full measured counts and reproducibility verification.

## Expected recommendation volume
**Measured (Phase 13)**: see Target latency below — this dataset size ranks ~1,000 jobs per
candidate request in ~0.3s single-request / ~0.06s per candidate in batch evaluation (after the
Phase 9 N+1 fix). No real-world volume target exists to compare against (synthetic project, no
production traffic), so this is an internal-consistency measurement, not a validated capacity
number.

## Target latency
**Measured (Phase 13)**, not just a working target anymore: single-request latency ~0.3s;
under concurrent load (20 concurrent requests) p50 0.34s / p95 0.57s after fixing a single-
worker-process bottleneck (was p50 1.33s / p95 1.84s with 1 worker — see
`docs/production-readiness.md` for the full load test writeup and the fix). This is one
measurement at one point in time against one endpoint, not a full capacity plan — see that
doc's "what's still missing" section for what a production latency SLA would still need
(sustained load, multiple endpoints, production-scale concurrency).

## Batch refresh requirements
Still not defined with a real cadence — `jme-dataquality`'s Kubernetes CronJob
(`k8s/base/cronjob-dataquality.yaml`) runs on a placeholder daily schedule, not one derived from
an actual upstream data-refresh cadence (no real upstream data source exists in this synthetic
project to derive one from). Left open deliberately rather than inventing a number with nothing
to base it on.

## Security requirements
- No secrets in source control (enforced from commit 1 via `.gitignore`)
- AuthN/authZ required before the API is usable beyond localhost health checks (Phase 6)
- Synthetic data only — no real PII ever enters this system
- Container/dependency scanning introduced in Phase 8, not deferred to the end

## Local and cloud deployment goals
- Local: Docker Compose on this dev machine, and a local Kubernetes (`kind`) cluster (Phase 12)
- Offline/disconnected deployment: validated with zero network access, Phase 10
- Cloud (Azure): **resolved (Phase 11)** — no real Azure subscription used; an Azure-shaped
  Kubernetes overlay exists (`k8s/overlays/azure`, `docs/kubernetes.md`) but was never applied to
  real infrastructure (see assumption A1)

## Budget constraints
No paid cloud resources were provisioned at any point — **honored throughout**: Phase 11 resolved
the Azure question as "local-only, zero cloud cost," so this was never tested against a real
spend decision. Local development (existing hardware, open-source tooling) remained free.

## Acceptance criteria
See `docs/prd.md` Section 9 (Definition of Done) — functional, engineering, operational,
security, and architecture checklists, to be revisited and checked off per phase.

## Known / unknown / assumed
| Item | Status |
|---|---|
| Hardware specs | Known (measured) — `docs/hardware-inventory.md` |
| Use cases A–F | Known — defined in PRD; A/B/D/E built and verified, C/F deferred (see `docs/progress.md`) |
| Dataset scale | Known (measured) — Phase 2, `docs/data-model.md` |
| Target latency numbers | Known (measured, one scenario) — Phase 13, `docs/production-readiness.md` |
| Azure availability | Known — resolved Phase 11: no real Azure subscription used, local-only + Azure-shaped K8s overlay (`docs/kubernetes.md`) |
| Batch refresh cadence | Still unknown — `jme-dataquality`'s CronJob schedule (`k8s/base/cronjob-dataquality.yaml`) is a placeholder (daily), not a decided real-world cadence; no upstream data source exists to derive one from |
