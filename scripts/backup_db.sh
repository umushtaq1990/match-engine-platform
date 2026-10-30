#!/usr/bin/env bash
# Backs up both Postgres databases in this stack (jme: core app data,
# mlflow: experiment tracking) to timestamped custom-format dumps, via
# pg_dump run inside the running db container - no separate pg_dump
# installation needed on the host. Custom format (-Fc), not plain SQL,
# because it's what pg_restore needs and it includes the `CREATE EXTENSION`
# statement for pgvector automatically (verified, not assumed - see
# docs/backup-and-recovery.md). Run explicitly: ./scripts/backup_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="backups"
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
CONTAINER="jme-db-1"

mkdir -p "$BACKUP_DIR"

for db in jme mlflow; do
  out="$BACKUP_DIR/${db}_${TIMESTAMP}.dump"
  echo "Backing up database '$db' -> $out"
  docker exec "$CONTAINER" pg_dump -U jme -d "$db" -Fc > "$out"
done

echo "Backup complete: $BACKUP_DIR/*_${TIMESTAMP}.dump"
