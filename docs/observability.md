# Observability

Status: v0.1 — API latency/error-rate/request-rate fully working and verified; container CPU/RAM
partially verified (see the Docker Desktop limitation below).

## Purpose & Scope

Covers infrastructure-level observability (CPU/RAM/latency/error-rate/batch-time) per
`docs/prd.md`'s requirement — distinct from `docs/data-quality.md`'s content-level checks on the
data itself.

## Architecture & Design

Three always-on services added to `docker-compose.yml` alongside `mlflow` (same rationale:
continuously scraping, not a one-off job):

- **`prometheus`** (`prom/prometheus:v2.55.1`, port 9090) — scrapes `api:8000/metrics`,
  `analytics:8001/metrics` (Phase 14), and `cadvisor:8080/metrics` every 10s
  (`docker/prometheus/prometheus.yml`).
- **`cadvisor`** (`gcr.io/cadvisor/cadvisor:v0.49.1`, port 8081) — per-container resource metrics.
- **`grafana`** (`grafana/grafana:11.3.1`, port 3000) — one provisioned dashboard ("JME Overview"),
  provisioned datasource pointing at `prometheus:9090` (`docker/grafana/provisioning/`). Anonymous
  viewer access is enabled for local convenience; the admin password comes from
  `GRAFANA_ADMIN_PASSWORD` in `.env`.

## Implementation

`app/api/main.py` is instrumented with `prometheus-fastapi-instrumentator`, exposing `/metrics`:
request count and latency histograms labeled by route/method/status — the app-level half of the
requirement (latency, error rate, request rate). Container CPU/RAM come from cAdvisor instead of
hand-rolled app metrics — the standard pattern, and it covers every container (db, mlflow, etc.),
not just the API.

**Batch-time (data quality / evaluation runs)**: not pushed to Prometheus — `data_quality_runs`/
`evaluation_runs` already record `run_at` (and implicitly duration, via application-level timing
already printed to stdout) in Postgres, which is sufficient for this project's scale (infrequent,
manually-triggered one-off jobs, not a continuous pipeline needing real-time alerting). Pushing
these to Prometheus via a Pushgateway is a reasonable future addition, not built here — not needed
until these jobs run on an actual schedule (Phase 12's CronJob plan), at which point "did last
night's run succeed and how long did it take" becomes an alerting question rather than something to
read from a table.

## Verification & Results

- `GET /metrics` on the live `api` container returns real Prometheus-format metrics; confirmed
  `http_requests_total`/`http_request_duration_seconds_*` update after real calls.
- Prometheus `/api/v1/targets`: both `api` and `cadvisor` jobs report `health: up`.
- Request-rate and latency (`histogram_quantile`) PromQL queries used by the dashboard panels
  return real data when queried directly against Prometheus.
- Grafana: the "JME Overview" dashboard and the Prometheus datasource are both provisioned
  correctly (`GET /api/dashboards/uid/jme-overview` returns all 5 panels); confirmed a query
  proxied *through Grafana's own datasource API* (not just directly against Prometheus) returns
  real target health data (`up{job="api"} 1`, `up{job="cadvisor"} 1`).

**A real, investigated limitation: cAdvisor per-container metrics on Docker Desktop (Windows)**.
`cadvisor`'s own `/metrics` and the Prometheus `up` check both report healthy, but per-container
breakdowns (`container_memory_usage_bytes{name="..."}`, etc.) are **not populated** on this
specific host — only cgroup-aggregate entries (`/`, `/docker`, `/kubepods`) show up, with no
per-container `name` label. Investigated, not just noticed: `docker exec`'d into the `cadvisor`
container and found `/var/lib/docker` uses a `containerd`-backed layout (no `overlay2/` directory
the classic Docker storage driver would have) — consistent with Docker Desktop for Windows/Mac's
containerd image store, which cAdvisor's container discovery doesn't handle the same way it does
on a native Linux Docker host with the classic driver. This is a documented, known class of
cAdvisor-on-Docker-Desktop issue, not a misconfiguration in this project — the same compose
service is expected to work as designed on a native Linux host, which is exactly the target for
this project's later deployment phases (offline deployment, Kubernetes both assume Linux hosts, not
Docker Desktop).

**So the "Container CPU usage" / "Container memory usage" dashboard panels are wired correctly but
will show no data on a Windows/Mac Docker Desktop dev machine.** The API latency/error-rate/
request-rate panels are unaffected and fully functional everywhere.

## Known Limitations

- No alerting rules (Alertmanager) — dashboards only, no paging/notification on thresholds.
- No load testing yet — Prometheus can now measure p50/p95/p99 under real load, but no load has
  been generated to measure it under (assumption A6 in `docs/assumptions-register.md` still open).
- Container CPU/RAM panels don't render on Docker Desktop (see above) — expected to work on a
  native Linux host, not yet verified there since this project has no such host available.
- **No logging pipeline at all** — this stack is metrics-only (Prometheus/cAdvisor/Grafana). No
  application logs are aggregated, searched, or correlated anywhere; each service's stdout only
  goes to `docker compose logs`. There is no Loki/ELK/equivalent, and no structured log format
  shared across services.

## Roadmap

- **Centralized logging package**: a shared logging module every service (`api`, `analytics`,
  `extraction`, `frontend`, batch jobs) imports, emitting structured (JSON) logs with a consistent
  schema (timestamp, service name, request/correlation id, level) instead of each service's current
  ad-hoc stdout prints. Pairs naturally with a log-aggregation backend (e.g. Loki, since it shares
  Grafana as a single pane of glass with the existing Prometheus metrics) once there's a real need
  to search/correlate logs across services rather than just tail one container's output — not built
  yet, scoped deliberately small until that need is concrete, per this project's "measure before
  adding complexity" discipline (see `docs/architecture.md` §3).

## Related Documents

- [architecture.md](architecture.md) — overall system overview
- [data-quality.md](data-quality.md) — content-level checks (separate concern)
- [infrastructure-sizing.md](infrastructure-sizing.md) — measured resource footprint
- [production-readiness.md](production-readiness.md) — real load-test results once load testing was built
- [progress.md](progress.md) — full build history
