# Requirements — JME Portfolio Platform

Status: v0.1 — captures what is known today; updated as phases progress.

## Functional requirements
See `docs/PRD.md` Section 5 (Use Cases A–F) for the authoritative list. Summary:
- Candidate-to-job and job-to-candidate ranked matching with explanations
- Free-text skill extraction into a canonical taxonomy
- Skill-gap analysis distinguishing "not recorded" vs "assessed and absent"
- Course/learning recommendations against identified gaps
- Similarity/semantic search (jobs, candidates, courses) and fuzzy title/skill matching

## Expected users
- Internal advisor/case-worker persona reviewing recommendations (primary)
- Platform operator/engineer persona deploying and monitoring the system (secondary)
- No public/self-service users in v1 (see PRD Section 6, scope boundaries)

## Synthetic dataset size
Not yet fixed. To be decided in Phase 2 based on:
- What is large enough to make matching/ranking non-trivial
- What stays comfortably within measured hardware limits (16GB RAM, 593GB disk free)
Initial working assumption (to validate in Phase 2): low thousands of candidates, low thousands
of job postings, a few hundred skills/occupations, a few hundred courses.

## Expected recommendation volume
Not yet measured. Will be derived from dataset size once Phase 2/3 are built, then used to drive
Phase 9 load testing. No volume target is assumed before measurement.

## Target latency
Working target (not yet validated): API responses for precomputed recommendations in the
low hundreds of milliseconds; free-text skill extraction and any on-demand scoring kept
measurably bounded. To be confirmed with real benchmarks in Phase 9.

## Batch refresh requirements
Not yet defined. Likely a periodic (e.g., nightly) batch recommendation recompute, consistent
with PRD's "precomputed, not request-time" design principle. Exact cadence to be decided once
batch run time is measured in Phase 3/9.

## Security requirements
- No secrets in source control (enforced from commit 1 via `.gitignore`)
- AuthN/authZ required before the API is usable beyond localhost health checks (Phase 6)
- Synthetic data only — no real PII ever enters this system
- Container/dependency scanning introduced in Phase 8, not deferred to the end

## Local and cloud deployment goals
- Local: Docker Compose on this dev machine (confirmed working as of Milestone 1)
- Offline/disconnected deployment: explicit goal per PRD, validated in Phase 10
- Cloud (Azure): undecided — see `docs/assumptions-register.md`

## Budget constraints
No paid cloud resources are to be provisioned without explicit approval. Local development is
free (existing hardware, open-source tooling). Any Azure spend is a future decision point at
Phase 11, not assumed now.

## Acceptance criteria
See `docs/PRD.md` Section 9 (Definition of Done) — functional, engineering, operational,
security, and architecture checklists, to be revisited and checked off per phase.

## Known / unknown / assumed
| Item | Status |
|---|---|
| Hardware specs | Known (measured) — `docs/hardware-inventory.md` |
| Use cases A–F | Known — defined in PRD |
| Dataset scale | Unknown — Phase 2 decision |
| Target latency numbers | Assumed placeholder — Phase 9 validation |
| Azure availability | Unknown — deferred decision |
| Batch refresh cadence | Unknown — Phase 3/9 decision |
