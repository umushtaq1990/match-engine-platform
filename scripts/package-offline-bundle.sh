#!/usr/bin/env bash
# Packages everything a disconnected (air-gapped) host needs to run this
# stack: every image this compose file references, pre-built/pulled and
# saved to a tar, plus the compose file, env template, and the config files
# images alone don't carry (postgres init script, prometheus.yml, grafana
# provisioning). See docs/offline-deployment.md for how this is verified and
# how to use the bundle on the target host.
set -euo pipefail
cd "$(dirname "$0")/.."

BUNDLE_DIR="dist/offline-bundle"
IMAGES=(
  jme-api:latest
  jme-migrate:latest
  jme-seed:latest
  jme-dataquality:latest
  jme-mlflow:latest
  postgres:16
  prom/prometheus:v2.55.1
  gcr.io/cadvisor/cadvisor:v0.49.1
  grafana/grafana:11.3.1
)

echo "Building all service images (requires network for this step only)..."
docker compose --profile tools build

echo "Pulling third-party images not built locally..."
docker compose pull db prometheus cadvisor grafana

rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR"

echo "Saving ${#IMAGES[@]} images to $BUNDLE_DIR/images.tar (this takes a while)..."
docker save -o "$BUNDLE_DIR/images.tar" "${IMAGES[@]}"

echo "Copying compose file and config (not baked into any image)..."
cp docker-compose.yml "$BUNDLE_DIR/"
cp .env.example "$BUNDLE_DIR/"
cp -r docker/postgres-init "$BUNDLE_DIR/postgres-init"
cp -r docker/prometheus "$BUNDLE_DIR/prometheus"
cp -r docker/grafana "$BUNDLE_DIR/grafana"

cat > "$BUNDLE_DIR/README.md" <<'EOF'
# Offline deployment bundle

On the disconnected target host (Docker Engine + Compose already installed —
see docs/offline-deployment.md for why that one dependency isn't, and can't
be, bundled):

1. `docker load -i images.tar`
2. `cp .env.example .env` and edit the placeholder values
3. docker-compose.yml's `build:` blocks don't need removing — the images
   are already loaded and tagged, so compose uses them as-is without
   rebuilding. `--pull never` guarantees nothing is fetched from the network
   (it must come before the service name for `run`, after `up`/`run` but
   before the service list either way):
   - `docker compose up -d --pull never db`
   - `docker compose run --rm --pull never migrate`
   - `docker compose run --rm --pull never seed`
   - `docker compose run --rm --pull never dataquality`
   - `docker compose up -d --pull never api mlflow prometheus cadvisor grafana`

This directory's `postgres-init/`, `prometheus/`, `grafana/` must sit next to
docker-compose.yml exactly as they do here — the compose file's volume
mounts reference them by relative path.
EOF

echo "Done. Bundle at $BUNDLE_DIR ($(du -sh "$BUNDLE_DIR" | cut -f1))."
