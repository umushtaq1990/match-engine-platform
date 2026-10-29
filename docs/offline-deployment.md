# Offline / Disconnected Deployment (Phase 10)

Status: v0.1 — verified end-to-end on this dev machine.

## What "disconnected deployment validated" means here

Per `docs/prd.md`'s quality requirement ("Runs identically from a clean checkout via Docker
Compose; disconnected deployment validated"): prove the full stack can be built once on a
network-connected machine, transferred as plain files, and brought up on a target host with **zero
network access** — no registry pulls, no `pip install`, nothing reached over the internet at deploy
time. This matters for the kind of environment a real enterprise architecture has to account for
(regulated/air-gapped networks), not just convenience.

## How it works

`scripts/package-offline-bundle.sh`:
1. Builds all 5 custom images (`api`, `migrate`, `seed`, `dataquality`, `mlflow`) and pulls the 4
   third-party images (`postgres:16`, `prom/prometheus`, `cadvisor`, `grafana`) — this step still
   needs network, by definition (something has to fetch the bits once).
2. `docker save`s all 9 images into one `images.tar`.
3. Copies `docker-compose.yml`, `.env.example`, and every config file an image doesn't carry itself
   (`docker/postgres-init/`, `docker/prometheus/`, `docker/grafana/`) into `dist/offline-bundle/`.

That bundle directory (plus Docker Engine + Compose already installed on the target — the one
prerequisite that genuinely can't be bundled, since it's the runtime the bundle depends on) is
everything a disconnected host needs.

## Verified (not assumed) — the actual test run

1. `docker compose --profile tools down -v` — full teardown.
2. `docker rmi -f` all 9 images used by this stack — confirmed gone via `docker images`.
3. `docker load -i dist/offline-bundle/images.tar` — confirmed all 9 reloaded, from the tar alone.
4. Brought the entire stack up using **only** `--pull never` (Compose's flag that errors loudly if
   anything would need fetching, instead of silently pulling) — db → migrate (all 3 migrations
   applied) → seed (2000/1000 rows) → dataquality (10/10 checks passed) → api/mlflow/prometheus/
   cadvisor/grafana (all healthy, real authenticated API call returned 200, Prometheus reported
   both scrape targets `up`).

Nothing failed, nothing silently reached the network — `--pull never` would have errored instead of
masking a pull if anything had been missing from the loaded tar.

## Known limitations

- Docker Engine + Compose itself isn't bundled — it's the assumed baseline runtime, the same way an
  offline Kubernetes deployment still assumes `kubectl`/a running cluster exist. Installing Docker
  itself offline is a separate, well-documented OS-level concern, out of this project's scope.
- The bundle (~756MB, mostly `postgres`/`grafana`/`cadvisor` base layers) isn't committed to git —
  `dist/` is git-ignored. Regenerate with `./scripts/package-offline-bundle.sh` whenever deploying.
- Not tested on an actual air-gapped machine (no such hardware available in this project) — tested
  by deleting every image from the same host instead and reloading only from the tar, which
  exercises the same code path Docker uses to decide whether to pull (`--pull never`), just without
  physically disconnecting a NIC. The remaining gap (unavailable DNS/registry reachability) is a
  property of the network, not of this bundle or Compose's pull logic.
