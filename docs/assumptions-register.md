# Assumptions Register

Tracks every assumption made so far, why it was made, and what would invalidate it. Review and
update at the start of each new phase.

| ID | Assumption | Rationale | Risk if wrong | Validation plan |
|---|---|---|---|---|
| A1 | Azure subscription availability is undecided; design proceeds local-first | User has not confirmed Azure access | Phase 11 cloud deployment may need re-architecture for a different target (e.g., on-prem only) | Ask again before Phase 11 starts |
| A2 | Docker Desktop WSL2 VM allocation (16 CPU / ~7.4GB RAM) is sufficient through Phase 2–4 | Measured idle footprint is tiny (~30MB/container) | Running Postgres + Qdrant + API + a local model concurrently may exceed 7.4GB | Monitor `docker stats` as services are added; raise `.wslconfig` limit only when actually hit |
| A3 | 8GB VRAM supports only small quantized (7B-class) local LLMs, not larger models | Measured via `nvidia-smi` | If skill extraction/explanation quality needs a larger model, local inference may be infeasible | Defer LLM use until rule-based baseline is proven insufficient (Phase 3/4); treat LLM serving as swappable/mockable |
| A4 | ~~Synthetic dataset scale not yet fixed~~ **RESOLVED (Phase 2)**: 2,000 candidates, 1,000 jobs, 150 occupations, 100 courses, 64 curated skills (not 300 — see below) | Scale chosen for fast dev-machine iteration; skill count reduced from the original "~300" placeholder because 64 real, meaningful, categorized skill names serve matching/explainability better than padding to an arbitrary round number with meaningless names | None — generator is deterministic (verified: identical MD5 hash of candidate data across two runs) and measured row counts match target imperfection rates | Done — see `docs/data-model.md` for full measured counts |
| A5 | Rule-based/deterministic matching is sufficient for v1; embeddings/LLM only added if they show measurable improvement | Explicit instruction in project brief; avoids premature complexity | Could under-deliver on "semantic" capability if rules prove too weak | Phase 4 evaluation report will state measured delta between rule-based and semantic approaches before any decision to adopt |
| A6 | Target latency figures in `requirements.md` are placeholders, not measured | No load testing has occurred yet (Phase 9 not started) | Could be significantly off from what's achievable on this hardware | Replace placeholders with measured p50/p95/p99 once Phase 9 load tests run |
| A7 | Dev Postgres credentials in `docker-compose.yml` are acceptable as plaintext for local-only dev | No external network exposure at this stage; single-user dev machine | Would be unacceptable if compose file were deployed beyond localhost | Move to `.env`-based secrets before any shared/deployed environment (Phase 7+) |
| A8 | No confidential material from prior employer engagements has been used in design or data | Explicit user confirmation; files in parent `EY/` folder were not opened or read | If violated, project could not ethically be published as a portfolio piece | Maintained by discipline: never open/reference files outside the `JME/` project folder that are flagged confidential |

## Review cadence
Revisit this file at the start of each new phase (per `docs/progress.md`) and at any point an
assumption is confirmed, invalidated, or superseded by a measurement.
