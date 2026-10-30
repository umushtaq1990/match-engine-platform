# Backup & Recovery

Status: v1 — implemented and verified end-to-end, closing the gap `docs/production-readiness.md`
flagged: *"No backup/restore procedure has been built or tested for the Postgres volume."*

## What's backed up

Both databases living in the single Postgres instance this project runs: `jme` (core app data —
candidates, jobs, embeddings, extraction/analytics/evaluation results, everything under the
`public` and `analytics` schemas) and `mlflow` (experiment tracking backend store). One dump file
per database, not one combined dump — restoring one doesn't require touching the other.

## Scripts

- **`scripts/backup_db.sh`** — runs `pg_dump -Fc` (custom format) inside the running `db`
  container for both databases, writing timestamped dumps to `backups/` (git-ignored — these are
  real local data, never committed). Custom format, not plain SQL, because it's what `pg_restore`
  consumes and — verified, not assumed — it automatically includes the `CREATE EXTENSION`
  statement for `pgvector`, so a restore recreates the extension without a separate manual step.
- **`scripts/restore_db.sh <database-name> <dump-file> [--yes]`** — drops and recreates the named
  database, then `pg_restore`s into it. Destructive by nature (that's what a restore is);
  interactive confirmation unless `--yes` is passed. Terminates existing connections to the target
  database first, since Postgres refuses to drop a database with active connections.

## Verified end-to-end (not assumed)

Run for real against this project's actual dev data, never against a disposable copy — because the
whole point was proving the real mechanics work, not a toy example:

1. `./scripts/backup_db.sh` produced real dumps: `jme_<timestamp>.dump` (7.6MB) and
   `mlflow_<timestamp>.dump` (139KB).
2. Rather than risk the real `jme` database for the restore half of this test, created a disposable
   target database (`jme_restore_test`) in the same running container and restored the `jme` dump
   into *that* — exercising the real dump file and the real pgvector-enabled image, without ever
   touching the real database.
3. **Row counts matched exactly** between `jme` and the restored copy, across every kind of table
   this project has — core domain, derived/eval tables, and a different schema:

   | Table | Original | Restored |
   |---|---|---|
   | `candidates` | 2000 | 2000 |
   | `jobs` | 1000 | 1000 |
   | `entity_embeddings` | 3240 | 3240 |
   | `analytics.behavioral_events` | 28226 | 28226 |
   | `extraction_runs` | 3 | 3 |

4. **The pgvector extension and its HNSW index survived the restore correctly** — confirmed via
   `\dx vector` (extension present, version 0.8.7) and `\d entity_embeddings` (the
   `ix_entity_embeddings_ann` HNSW index present), not just "the table exists."
5. Disposable restore target dropped afterward; the real `jme` database was never modified by this
   verification.

## A real one-time local fix encountered along the way

The pgvector image swap (Phase 17) left `template1`'s collation version mismatched against the new
base image's glibc (same root cause as the `jme`/`mlflow` collation warnings fixed in that phase) —
`CREATE DATABASE` for the disposable restore target failed until `template1` (and `postgres`, the
default admin database) were also refreshed with `ALTER DATABASE ... REFRESH COLLATION VERSION`.
Purely a local-dev-environment artifact of swapping a running container's image against an existing
data directory — a fresh checkout/CI run never hits this, since it always creates a new volume.

## Known limitations (v1)

- No automated/scheduled backup job — this is a manual script, run on demand. A real deployment
  would cron this (or use managed-database point-in-time recovery instead of pg_dump entirely).
- No off-host backup storage — dumps land in the local `backups/` directory only. A real backup
  strategy keeps copies somewhere the same disk failure can't take out.
- No restore test automated into CI — this was a one-time manual verification, not a repeatable
  CI check. Worth revisiting if backup/restore logic changes again.
