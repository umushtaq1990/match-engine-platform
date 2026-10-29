# Observability (Phase 9)

Status: v0.1 — API latency/error-rate/request-rate fully working and verified; container CPU/RAM
partially verified (see the Docker Desktop limitation below).

## What was built

Per `docs/prd.md`'s requirement ("CPU/RAM/latency/error-rate/batch-time measurable via
Prometheus+Grafana"), three always-on services were added to `docker-compose.yml` alongside
`mlflow` (same rationale: continuously scraping, not a one-off job):

- **`prometheus`** (`prom/prometheus:v2.55.1`, port 9090) — scrapes `api:8000/metrics` and
  `cadvisor:8080/metrics` every 10s (`docker/prometheus/prometheus.yml`).
- **`cadvisor`** (`gcr.io/cadvisor/cadvisor:v0.49.1`, port 8081) — per-container resource metrics.
- **`grafana`** (`grafana/grafana:11.3.1`, port 3000) — one provisioned dashboard ("JME Overview"),
  provisioned datasource pointing at `prometheus:9090` (`docker/grafana/provisioning/`). Anonymous
  viewer access is enabled for local convenience; the admin password comes from
  `GRAFANA_ADMIN_PASSWORD` in `.env`.

`app/api/main.py` is instrumented with `prometheus-fastapi-instrumentator`, exposing `/metrics`:
request count and latency histograms labeled by route/method/status — the app-level half of the
requirement (latency, error rate, request rate). Container CPU/RAM come from cAdvisor instead of
hand-rolled app metrics — the standard pattern, and it covers every container (db, mlflow, etc.),
not just the API.

## Verified (not assumed)

- `GET /metrics` on the live `api` container returns real Prometheus-format metrics; confirmed
  `http_requests_total`/`http_request_duration_seconds_*` update after real calls.
- Prometheus `/api/v1/targets`: both `api` and `cadvisor` jobs report `health: up`.
- Request-rate and latency (`histogram_quantile`) PromQL queries used by the dashboard panels
  return real data when queried directly against Prometheus.
- Grafana: the "JME Overview" dashboard and the Prometheus datasource are both provisioned
  correctly (`GET /api/dashboards/uid/jme-overview` returns all 5 panels); confirmed a query
  proxied *through Grafana's own datasource API* (not just directly against Prometheus) returns
  real target health data (`up{job="api"} 1`, `up{job="cadvisor"} 1`).

## Known limitation: cAdvisor per-container metrics on Docker Desktop (Windows)

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
this project's later deployment phases (Phase 10 offline deployment, Phase 12 Kubernetes both
assume Linux hosts, not Docker Desktop).

**So the "Container CPU usage" / "Container memory usage" dashboard panels are wired correctly but
will show no data on a Windows/Mac Docker Desktop dev machine.** The API latency/error-rate/
request-rate panels are unaffected and fully functional everywhere.

## Batch-time (data quality / evaluation runs)

Not pushed to Prometheus — `data_quality_runs`/`evaluation_runs` already record `run_at` (and
implicitly duration, via application-level timing already printed to stdout) in Postgres, which is
sufficient for this project's scale (infrequent, manually-triggered one-off jobs, not a continuous
pipeline needing real-time alerting). Pushing these to Prometheus via a Pushgateway is a reasonable
future addition, not built here — not needed until these jobs run on an actual schedule (Phase 12's
CronJob plan), at which point "did last night's run succeed and how long did it take" becomes an
alerting question rather than something to read from a table.

## Known limitations / not done in this phase

- No alerting rules (Alertmanager) — dashboards only, no paging/notification on thresholds.
- No load testing yet — Prometheus can now measure p50/p95/p99 under real load, but no load has
  been generated to measure it under (assumption A6 in `docs/assumptions-register.md` still open).
- Container CPU/RAM panels don't render on Docker Desktop (see above) — expected to work on a
  native Linux host, not yet verified there since this project has no such host available.
