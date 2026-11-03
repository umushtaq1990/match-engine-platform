# Observability and Logging (Prometheus, Grafana, cAdvisor, Loki, Promtail)

Status: v1 — metrics side fully working and verified; logging side implemented and
verified end-to-end; container CPU/RAM partially verified (see the Docker Desktop
limitation below).

Level-3 diagram (cropped from the master Technical Architecture diagram):
[`observability-and-logging.drawio`](observability-and-logging.drawio).

This covers everything this project uses to answer "is the system healthy and what
is it doing right now": metrics (Prometheus/cAdvisor/Grafana) and logs
(Loki/Promtail). Two previously separate docs (`observability.md`, `logging.md`) are
merged here because they share one Grafana front end and one purpose: a single pane
of glass for the running stack.

## Part 1 — Metrics (Prometheus, cAdvisor, Grafana)

### Purpose & Scope

Covers infrastructure-level observability (CPU/RAM/latency/error-rate/batch-time) per
the product brief's requirement, distinct from
[data-platform/data-quality.md](data-platform/data-quality.md)'s content-level checks
on the data itself.

### Architecture & Design

Three always-on services added to `docker-compose.yml` alongside `mlflow` (same
rationale: continuously scraping, not a one-off job):

- `prometheus` (`prom/prometheus:v2.55.1`, port 9090) scrapes `api:8000/metrics`,
  `analytics:8001/metrics`, and `cadvisor:8080/metrics` every 10s
  (`docker/prometheus/prometheus.yml`).
- `cadvisor` (`gcr.io/cadvisor/cadvisor:v0.49.1`, port 8081) provides per-container
  resource metrics.
- `grafana` (`grafana/grafana:11.3.1`, port 3000) ships one provisioned dashboard
  ("JME Overview"), with provisioned datasources pointing at `prometheus:9090` and
  (Part 2 below) `loki:3100` (`docker/grafana/provisioning/`). Anonymous viewer
  access is enabled for local convenience; the admin password comes from
  `GRAFANA_ADMIN_PASSWORD` in `.env`.

### Implementation

`app/api/main.py` is instrumented with `prometheus-fastapi-instrumentator`, exposing
`/metrics`: request count and latency histograms labeled by route/method/status, the
app-level half of the requirement (latency, error rate, request rate). Container
CPU/RAM come from cAdvisor instead of hand-rolled app metrics, the standard pattern,
and it covers every container (db, mlflow, etc.), not just the API.

Batch-time (data quality / evaluation runs) is not pushed to Prometheus —
`data_quality_runs`/`evaluation_runs` already record `run_at` (and implicitly
duration, via application-level timing already printed to stdout) in Postgres, which
is sufficient for this project's scale (infrequent, manually-triggered one-off jobs,
not a continuous pipeline needing real-time alerting). Pushing these to Prometheus
via a Pushgateway is a reasonable future addition, not built here.

### Verification & Results

- `GET /metrics` on the live `api` container returns real Prometheus-format metrics;
  confirmed `http_requests_total`/`http_request_duration_seconds_*` update after
  real calls.
- Prometheus `/api/v1/targets`: both `api` and `cadvisor` jobs report `health: up`.
- Request-rate and latency (`histogram_quantile`) PromQL queries used by the
  dashboard panels return real data when queried directly against Prometheus.
- Grafana: the "JME Overview" dashboard and the Prometheus datasource are both
  provisioned correctly (`GET /api/dashboards/uid/jme-overview` returns all 5
  panels); confirmed a query proxied through Grafana's own datasource API (not just
  directly against Prometheus) returns real target health data (`up{job="api"} 1`,
  `up{job="cadvisor"} 1`).

A real, investigated limitation: cAdvisor per-container metrics on Docker Desktop
(Windows). `cadvisor`'s own `/metrics` and the Prometheus `up` check both report
healthy, but per-container breakdowns
(`container_memory_usage_bytes{name="..."}`, etc.) are not populated on this
specific host, only cgroup-aggregate entries (`/`, `/docker`, `/kubepods`) show up,
with no per-container `name` label. Investigated, not just noticed: `docker exec`'d
into the `cadvisor` container and found `/var/lib/docker` uses a `containerd`-backed
layout (no `overlay2/` directory the classic Docker storage driver would have),
consistent with Docker Desktop for Windows/Mac's containerd image store, which
cAdvisor's container discovery doesn't handle the same way it does on a native
Linux Docker host with the classic driver. This is a documented, known class of
cAdvisor-on-Docker-Desktop issue, not a misconfiguration in this project, the same
compose service is expected to work as designed on a native Linux host, which is
exactly the target for this project's later deployment phases.

So the "Container CPU usage" / "Container memory usage" dashboard panels are wired
correctly but will show no data on a Windows/Mac Docker Desktop dev machine. The API
latency/error-rate/request-rate panels are unaffected and fully functional
everywhere.

### Known Limitations

- No alerting rules (Alertmanager), dashboards only, no paging/notification on
  thresholds.
- Container CPU/RAM panels don't render on Docker Desktop (see above), expected to
  work on a native Linux host, not yet verified there since this project has no
  such host available.

## Part 2 — Centralized Logging (Loki, Promtail)

### Purpose & Scope

Part 1's stack was metrics-only, no application logs were aggregated, searched, or
correlated anywhere, and no service shared a structured log format. This part closes
that gap: a shared logging package every always-on service imports, plus
Loki/Promtail so those logs are actually searchable through Grafana, not just
`docker compose logs <service>`.

Out of scope, by design: batch jobs (`app/evaluation/*`, `app/graph/*`,
`app/embeddings/*`, etc.) stay print-based for now, the same `app/common/logging.py`
module would work there too, but wiring every one-off job wasn't needed to close the
gap this part actually flagged (the always-on services with no log aggregation at
all). See Known Limitations.

### Architecture & Design

One shared package (`app/common/`), a deliberate exception to this project's
convention. Every other cross-cutting concern in this project (`deps.py`,
`rate_limit.py`) is duplicated per service specifically to avoid coupling one Docker
image's dependencies to another service's business logic (see those files' own
docstrings). Logging setup has zero business logic and zero extra dependencies
(stdlib `logging` only), the coupling argument that justifies duplication elsewhere
doesn't apply here, so this is the one place sharing a module makes sense. Scoped
narrowly on purpose: this doesn't set a precedent for sharing `deps.py`/
`rate_limit.py` too.

Stdout stays the transport. Every service already logged to stdout (just
unstructured, `print()` or unconfigured `logging.getLogger(__name__)` calls going
nowhere in particular). This part doesn't change that, it structures what already
goes to stdout as JSON, and lets Promtail (which tails container stdout via the
Docker socket) ship it to Loki. No service makes a network call to log anything; if
Loki is down, logging keeps working exactly as before, just unaggregated.

Loki + Promtail, not a heavier stack (ELK): Loki pairs directly with the Grafana
this project already runs for metrics, the same "single pane of glass" reasoning as
Part 1. Promtail's Docker service discovery means zero per-service config: it finds
every container via the Docker socket and labels logs by `compose_service`
automatically, the same zero-config-per-service property cAdvisor already has for
metrics.

### Implementation

- `app/common/logging.py`: `setup_logging(service_name)` configures the root logger
  with a hand-rolled JSON formatter (timestamp, level, service, logger name,
  message, optional `request_id`/exception traceback). Call once at process startup.
- `app/common/request_logging.py`: `add_request_logging(app, service_name)` is a
  FastAPI middleware logging one JSON line per request (method/path/status/
  duration), generating/propagating an `X-Request-ID` header for correlation.
- Wired into `app/api/main.py`, `app/analytics/main.py`, `app/extraction/main.py`
  (both `setup_logging` and `add_request_logging`), and
  `app/frontend/streamlit_app.py` (`setup_logging` only, Streamlit isn't a FastAPI
  app, no request middleware to add).
- `docker/Dockerfile.{api,analytics,extraction,frontend}`: each now `COPY`s
  `app/common/` (and `app/__init__.py`, where not already present) alongside the
  files they already copied, no other service needed any new dependency.
- `loki` (official image, filesystem storage, no clustering,
  `docker/loki/loki-config.yml`) plus `promtail` (official image, Docker-socket
  service discovery, `docker/promtail/promtail-config.yml`), both new
  `docker-compose.yml` services.
  `docker/grafana/provisioning/datasources/loki.yml` adds Loki as Grafana's second
  datasource.

### Verification & Results

- Real structured logs confirmed landing in Loki, not assumed from the wiring
  alone: triggered real `GET /health`/`/metrics` calls against `api`, waited for
  Promtail's 5s scrape interval, then queried Loki directly
  (`/loki/api/v1/query_range` with `{compose_service="api"}`) and got back the
  exact real JSON lines `app/common/request_logging.py` emits, e.g.
  `{"timestamp": "...", "level": "INFO", "service": "api", "logger": "request",
  "message": "GET /health -> 200 (0.66ms)", "request_id": "..."}`.
- Confirmed through Grafana's own datasource API, not just directly against Loki
  (same verification standard Part 1 already used for Prometheus): proxied the
  identical query through `/api/datasources/proxy/uid/<loki-uid>/loki/api/v1/query_range`
  and got the same real log lines back.
- Grafana datasource list confirms both Prometheus and Loki are provisioned (`GET
  /api/datasources` returns both, Loki not marked default, Prometheus stays the
  default, unchanged from before this part).
- Full project test suite re-run after wiring this in: no regressions (192 passing
  tests unaffected; 3 pre-existing local-dev-venv DB-schema failures in
  `tests/extraction/test_cv_routes_db.py` are unrelated environment drift, not
  caused by this change).

### Known Limitations

- Batch jobs are not wired in yet (`app/evaluation/*`, `app/graph/*`,
  `app/embeddings/*`, `app/ingestion/*`), they still use their existing
  `print()`-based output. The module would work there unchanged; just not done in
  this part, since the original gap was specifically about the always-on services
  having zero log aggregation.
- No log retention/rotation policy configured on Loki, filesystem storage,
  unbounded growth at this project's scale. Fine for a local demo, would need a
  real retention policy (`limits_config.retention_period` plus a compactor) before
  any real deployment.
- No alerting on logs (e.g. "page on N errors/minute"), same gap Part 1 already
  notes for metrics (no Alertmanager), logs inherit the same limitation.
- ID verification/correlation across services is log-only, not distributed
  tracing, `X-Request-ID` is generated/logged per request but not yet propagated by
  the Frontend or Gateway into the headers of the downstream calls they make, so
  correlating a single user action across `frontend -> gateway -> api` by request
  id isn't possible yet, only within one service's own logs.

## Roadmap

- Propagate `X-Request-ID` from the Gateway/Frontend into downstream calls, so a
  single user action can be traced across all three services by one id.
- Extend `setup_logging`/structured output to the batch jobs, once there's a
  concrete need to search/correlate their output rather than just read one job's
  stdout after running it.
- A real log retention policy before any deployment beyond this local demo.
- No load testing of the observability stack itself yet, Prometheus can measure
  p50/p95/p99 under real load, but see
  [operations/production-readiness.md](../operations/production-readiness.md) for
  the load generated so far.

## Resource Footprint and Scaling

This layer observes load, it doesn't generate or gate it — Prometheus/Grafana/cAdvisor/Loki/
Promtail all idle at single-digit-MB to low-GB footprints (Grafana and Prometheus especially
light; see [operations/infrastructure-sizing.md](../operations/infrastructure-sizing.md)) and none
of them sit in a user-facing request's hot path. The one real capacity question here is scrape/log
volume at a much larger deployment than this project's — not a concern at this project's measured
scale, and not built out further than that.

## Related Documents

- [data-platform/data-quality.md](data-platform/data-quality.md) — content-level
  checks (separate concern)
- [operations/infrastructure-sizing.md](../operations/infrastructure-sizing.md) —
  measured resource footprint
- [operations/production-readiness.md](../operations/production-readiness.md) —
  real load-test results
- [ingress-and-identity.md](ingress-and-identity.md) — the Gateway/Reverse
  Proxy/Load Balancer and SSO login gate this logging also covers
- [architecture.md](../architecture.md) — overall system architecture
- [progress.md](../progress.md) — full build history
