# JME Progress Log

## Phase 0/1 — Milestone 1: Hello JME (2026-10-28)

### Done
- Git repo initialized at project root.
- Directory structure: `app/`, `docs/`, `data/raw`, `data/seed`, `tests/`, `docker/`.
- Minimal FastAPI app (`app/main.py`) with `/health` and `/readiness`.
- `docker/Dockerfile.api` (python:3.12-slim base).
- `docker-compose.yml`: `api` + `db` (postgres:16) services, health checks, named volume `jme_db_data`.
- Built and started via `docker compose up -d --build`. Verified:
  - `GET /health` -> `{"status":"ok"}`
  - `GET /readiness` -> `{"status":"ready"}`
  - Both containers report `healthy`.

### Baseline measurements (idle, host: see hardware-inventory.md)
| Container | CPU % | Mem usage | Mem limit (Docker Desktop VM) |
|---|---|---|---|
| jme-api-1 | 7.74% (startup transient) | 32.7 MiB | 7.41 GiB |
| jme-db-1  | 6.07% (startup transient) | 30.8 MiB | 7.41 GiB |

Note: an unrelated pre-existing container (`livekit-server`) is running on the same Docker Desktop instance; not part of JME, left untouched.

### Decisions
- Dev Postgres credentials in `docker-compose.yml` are local-only placeholders (`jme` / `jme_dev_password`), not used beyond localhost. Will move to `.env` + secrets management before anything beyond local dev.
- Docker Desktop WSL2 VM currently allocates 16 CPUs / ~7.4 GiB RAM — sufficient for this milestone. Will revisit once Postgres + Qdrant + API + any local model run concurrently (host has 16GB RAM total, 8GB VRAM).

### Next
- Phase 1 continuation: pin dependency versions properly (requirements.txt done), add `.gitignore`, add `pytest` + first test for `/health`.
- Phase 0 docs: `docs/requirements.md`, `docs/assumptions-register.md`.
