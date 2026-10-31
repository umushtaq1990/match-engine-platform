# Data Quality Checks

Status: v0.1, implemented and verified.

## Purpose & Scope

Produces a regular, structured, queryable audit trail of the source data's health — distinct from
Phase 9 infrastructure observability (CPU/latency/error-rate). This is about the *content* of the
data, aimed at both engineers and non-technical stakeholders (compliance/governance visibility),
not just an engineering debugging tool.

## Architecture & Design

- Module: `app/ingestion/data_quality.py`. Lives alongside the synthetic data generator because
  producing data-quality reports is part of the ingestion/governance responsibility (brief
  Layer 2), distinct from the matching engine's own derived recommendation data (Layer 5).
- Deployment: a one-off job, same category as `migrate`/`seed` — **never** a long-running
  container. Reuses the `docker/Dockerfile.seed` image (same dependencies needed: SQLAlchemy +
  psycopg2), overriding its default command via `docker-compose.yml`'s `dataquality` service.
- Intended cadence: after any data load now (dev), on a recurring schedule once a real upstream
  feed exists (production) — a Kubernetes `CronJob` in Phase 12, not a `Deployment`.
- **Results storage**: two tables (`app/ingestion/quality_models.py`), migrated via Alembic —
  `data_quality_runs` (one row per run: timestamp, counts of passed/warned/failed checks) and
  `data_quality_check_results` (one row per check per run: check name, category, status,
  threshold(s), actual value, affected row count, human-readable detail). Storing results (rather
  than only printing them) is what makes a future Grafana/dashboard panel possible — "data quality
  failures over time" is explicitly called out in the brief's Phase 9 observability list, and this
  is the data source for it.

## Implementation

Run manually: `docker compose run --rm dataquality`

### Checks implemented (v0.1)

| Check | Category | Logic | Status rule |
|---|---|---|---|
| `candidate_occupation_completeness` | completeness | % candidates missing `primary_occupation_id` | warn ≥10%, fail ≥20% |
| `candidate_salary_completeness` | completeness | % candidates missing `desired_salary_min` | warn ≥25%, fail ≥40% |
| `job_salary_completeness` | completeness | % jobs missing `salary_min` | warn ≥20%, fail ≥35% |
| `duplicate_candidate_emails` | uniqueness | count of email values shared by >1 candidate | fail if >0 |
| `duplicate_candidate_identity` | uniqueness | count of (name, city, region) shared by >1 candidate | warn if >0 (informational, not a hard violation) |
| `orphan_candidate_skills` | referential_integrity | `candidate_skills` rows pointing to a missing candidate/skill | fail if >0 |
| `orphan_job_skills` | referential_integrity | `job_skills` rows pointing to a missing job/skill | fail if >0 |
| `salary_range_consistency` | range_validity | rows where min salary > max salary (candidates + jobs) | fail if >0 |
| `years_experience_range` | range_validity | candidates with `years_experience` outside [0, 50] | fail if >0 |
| `unused_skills_coverage` | taxonomy_coverage | % canonical skills never referenced anywhere | warn if >10% |

Thresholds are initial, documented judgment calls (set to sit comfortably outside the generator's
known target imperfection rates — see `docs/data-model.md`), not objectively correct — revisit
once real usage/evaluation data exists (Phase 4).

## Verification & Results

**First two runs, against the Phase 2 synthetic dataset** — all 10 checks **passed** on both run #1
and run #2 (run #2 executed from the containerized `dataquality` service, confirming the job works
identically in and out of Docker):

- 101/2000 candidates missing occupation (5.05% — within the 10% warn threshold)
- 305/2000 candidates missing salary (15.25% — within the 25% warn threshold)
- 89/1000 jobs missing salary (8.9% — within the 20% warn threshold)
- 0 duplicate emails, 0 duplicate identities, 0 orphaned FKs, 0 range violations
- 0/64 skills unused (full taxonomy coverage)

**A bug caught and fixed during build**: the `CheckStatus` enum's `pass` member had to be named
`pass_` in Python (`pass` is a reserved keyword). SQLAlchemy's `Enum()` defaults to storing the
Python member *name*, not its `.value` — so the column was initially storing the literal string
`"pass_"` in Postgres, which would have displayed incorrectly on any future dashboard. Fixed with
`values_callable=lambda obj: [e.value for e in obj]` so the stored value is the clean string
`"pass"`. Caught by actually querying the stored rows rather than only checking the Python-side
print output — a concrete instance of why "verify, don't assume" matters even for seemingly
cosmetic issues.

## Known Limitations

- Thresholds are not yet calibrated against any ground truth — they're reasonable starting points.
- No alerting/notification on fail status — Phase 9 added Prometheus/Grafana, but only for API
  latency/error-rate/CPU/RAM (`docs/observability.md`), not data-quality results, and Alertmanager
  was never wired up for any signal. Still an open gap, not just a future-phase placeholder.
- No dashboard for data-quality results specifically — results are queryable in Postgres
  (`data_quality_runs`/`data_quality_check_results`); Phase 9's Grafana dashboard covers API
  metrics only, a data-quality panel was never added to it.
- Duplicate-job detection intentionally not implemented as a hard check: multiple postings for
  the same role/company are normal, not a quality defect, so it wasn't included as a check.

## Related Documents

- [architecture.md](architecture.md) — overall system overview
- [data-model.md](data-model.md) — the schema these checks run against
- [observability.md](observability.md) — infrastructure-level observability (separate concern)
- [progress.md](progress.md) — full build history
