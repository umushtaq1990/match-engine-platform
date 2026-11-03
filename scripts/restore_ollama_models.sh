#!/usr/bin/env bash
# Restores the Ollama model cache from a tarball produced by
# backup_ollama_models.sh, so a wiped/recreated `ollama_data` volume doesn't
# mean re-pulling 1-2GB of model weights over the network. Restarts the
# ollama container afterward so it picks up the restored models (Ollama
# reads its model manifests at startup). See
# docs/architecture/ai-ml-and-mlops.md and docs/operations/backup-and-recovery.md
# for the equivalent Postgres procedure this one mirrors.
set -euo pipefail
cd "$(dirname "$0")/.."

CONTAINER="jme-ollama-1"

usage() {
  echo "Usage: $0 <tarball>"
  echo "  e.g. $0 backups/ollama-models_20261103T100000Z.tar.gz"
  exit 1
}

[ $# -lt 1 ] && usage
TARBALL="$1"
[ -f "$TARBALL" ] || { echo "Backup file not found: $TARBALL"; exit 1; }

echo "Restoring Ollama model cache from $TARBALL..."
MSYS_NO_PATHCONV=1 docker exec -i "$CONTAINER" tar xzf - -C /root/.ollama < "$TARBALL"

echo "Restarting ollama to pick up restored models..."
docker restart "$CONTAINER" >/dev/null
sleep 3

echo "Models present after restore:"
docker exec "$CONTAINER" ollama list
