# ADR 0001: Decouple ingestion, migrations, and API into separate images/services

Status: Accepted (2026-10-28)

## Problem

Phase 2 produced a single `app/` folder mixing three concerns: schema definition, synthetic data
generation, and the FastAPI service. All three were built and shipped as one image
(`docker/Dockerfile.api`), which:
- bundled Faker and Alembic into the production API image, even though the running API never
  uses either
- had no boundary reflecting the real-world ownership split between an upstream data-engineering
  pipeline (source-of-record data) and the matching engine's own logic
- would not have generalized to Kubernetes, where migrations run as a one-off Job, not inside a
  long-running Deployment

## Alternatives considered

1. **Keep one image, one `requirements.txt`.** Simplest, but couples unrelated lifecycles: an API
   pod would redeploy/rebuild for a change to the synthetic data generator, and would carry
   dependencies (Faker) it never calls at runtime.
2. **Split by folder only, same image.** Improves code readability but doesn't address the actual
   problem (image bloat, blast radius, deployment coupling).
3. **Split into separate images per concern, one-off jobs for migrate/seed.** Chosen.

## Decision

- `app/ingestion/` — source schema definitions (`source_models.py`) and the synthetic data
  generator. Stands in for an upstream data-engineering-owned pipeline. Built into
  `docker/Dockerfile.seed`, a dev/local-only image, never part of the production API.
- `alembic/` — schema migrations, applied by `docker/Dockerfile.migrate`, run as a one-off job
  (`docker compose run --rm migrate`), never as a long-running container.
- `app/api/` — the FastAPI service, built into `docker/Dockerfile.api`. Minimal runtime
  dependencies only.
- `app/db.py` — shared engine/session config, used by all three.
- Dependencies split into `requirements-base.txt` (shared), `requirements-api.txt`,
  `requirements-ingestion.txt`, `requirements-migrate.txt`, and a `requirements-dev.txt` that
  unions all of them for the local dev venv only.
- The generator no longer performs schema DDL (`drop_all`/`create_all`); it only truncates and
  repopulates data via `TRUNCATE ... RESTART IDENTITY CASCADE`. Schema ownership belongs
  exclusively to Alembic migrations.
- `docker-compose.yml`: `migrate` and `seed` are declared under `profiles: ["tools"]` so they
  never start on a plain `docker compose up`; they must be invoked explicitly.

## Consequences

- Verified: `migrate`, `seed`, and `api` each build and run independently as separate containers
  against a shared Postgres instance (tested end-to-end: fresh volume → migrate → seed → api →
  health checks pass, row counts match expected 2000 candidates / 1000 jobs).
- The API image is currently *larger* (289MB vs. the prior 245MB single image), because
  `requirements-api.txt` includes SQLAlchemy/psycopg2 in anticipation of Phase 3 (the API will
  need to query precomputed recommendations from Postgres). This is a deliberate trade-off, not a
  regression: the gain here is separation of responsibility and independent scalability/blast
  radius, not image size.
- This structure generalizes directly to Kubernetes (Phase 12): `migrate` becomes an init
  container or Job, `seed` either doesn't exist in a real deployment or becomes a scheduled
  CronJob fed by a real upstream system, and `api` becomes a horizontally scalable Deployment.

## Reconsider when

- If/when a real upstream data source replaces the synthetic generator, `app/ingestion/` is
  deleted entirely rather than evolved — it was always a stand-in, not a system to extend.
- If image size becomes a measured bottleneck (Phase 9/10), revisit whether the API truly needs
  SQLAlchemy bundled before Phase 3 lands, or whether that dependency should be added only when
  Phase 3 code actually uses it.
