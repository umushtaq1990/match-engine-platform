#!/usr/bin/env bash
# Restores a single database from a pg_dump custom-format backup produced by
# backup_db.sh. DESTRUCTIVE: drops and recreates the target database before
# restoring - confirmed interactively unless --yes is passed. See
# docs/backup-and-recovery.md for the verified end-to-end cycle.
set -euo pipefail
cd "$(dirname "$0")/.."

CONTAINER="jme-db-1"

usage() {
  echo "Usage: $0 <database-name> <dump-file> [--yes]"
  echo "  e.g. $0 jme backups/jme_20261030T120000Z.dump"
  exit 1
}

[ $# -lt 2 ] && usage

DB_NAME="$1"
DUMP_FILE="$2"
ASSUME_YES="${3:-}"

[ -f "$DUMP_FILE" ] || { echo "Dump file not found: $DUMP_FILE"; exit 1; }

if [ "$ASSUME_YES" != "--yes" ]; then
  read -r -p "This will DROP and recreate database '$DB_NAME' inside $CONTAINER. Continue? [y/N] " confirm
  case "$confirm" in
    y|Y) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

echo "Terminating existing connections to '$DB_NAME'..."
docker exec "$CONTAINER" psql -U jme -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();"

echo "Dropping and recreating database '$DB_NAME'..."
docker exec "$CONTAINER" psql -U jme -d postgres -c "DROP DATABASE IF EXISTS \"$DB_NAME\";"
docker exec "$CONTAINER" psql -U jme -d postgres -c "CREATE DATABASE \"$DB_NAME\" OWNER jme;"

echo "Restoring from $DUMP_FILE..."
docker exec -i "$CONTAINER" pg_restore -U jme -d "$DB_NAME" --no-owner < "$DUMP_FILE"

echo "Restore complete."
