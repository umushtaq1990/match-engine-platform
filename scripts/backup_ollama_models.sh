#!/usr/bin/env bash
# Backs up the Ollama model cache (everything docker-compose.yml's ollama
# service keeps in its named `ollama_data` volume: ~/.ollama/models) to a
# timestamped tarball on the host, same pattern as backup_db.sh. This exists
# because the model weights (qwen2.5:1.5b + nomic-embed-text + all-minilm,
# ~1-2GB combined) are expensive to re-pull on a slow connection and the
# named volume alone isn't a backup - it's gone the moment someone runs
# `docker compose down -v` or resets Docker Desktop, which is exactly what
# happened once during this project's own development (see
# docs/architecture/ai-ml-and-mlops.md). Run explicitly after
# `docker compose run --rm ollama-pull` has completed:
#   ./scripts/backup_ollama_models.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="backups"
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
CONTAINER="jme-ollama-1"
OUT="$BACKUP_DIR/ollama-models_${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "Models present before backup:"
docker exec "$CONTAINER" ollama list

echo "Backing up Ollama model cache -> $OUT"
MSYS_NO_PATHCONV=1 docker exec "$CONTAINER" tar czf - -C /root/.ollama models > "$OUT"

echo "Backup complete: $OUT ($(du -h "$OUT" | cut -f1))"
