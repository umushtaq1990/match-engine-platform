# Centralized Logging (structured JSON + Loki)

Status: v1 — implemented and verified end-to-end against the real running stack.

## Purpose & Scope

`docs/observability.md` documented a real gap: this stack was metrics-only (Prometheus/cAdvisor/
Grafana) — no application logs were aggregated, searched, or correlated anywhere, and no service
shared a structured log format. This phase closes that gap: a shared logging package every
always-on service imports, plus Loki/Promtail so those logs are actually searchable through
Grafana, not just `docker compose logs <service>`.

**Out of scope, by design**: batch jobs (`app/evaluation/*`, `app/graph/*`, `app/embeddings/*`,
etc.) stay print-based for now — the same `app/common/logging.py` module would work there too, but
wiring every one-off job wasn't needed to close the gap `docs/observability.md` actually flagged
(the always-on services with no log aggregation at all). See Known Limitations.

## Architecture & Design

**One shared package (`app/common/`), a deliberate exception to this project's convention.**
Every other cross-cutting concern in this project (`deps.py`, `rate_limit.py`) is *duplicated* per
service specifically to avoid coupling one Docker image's dependencies to another service's
business logic (see those files' own docstrings). Logging setup has zero business logic and zero
extra dependencies (stdlib `logging` only) — the coupling argument that justifies duplication
elsewhere doesn't apply here, so this is the one place sharing a module makes sense. Scoped
narrowly on purpose: this doesn't set a precedent for sharing `deps.py`/`rate_limit.py` too.

**Stdout stays the transport.** Every service already logged to stdout (just unstructured,
`print()` or unconfigured `logging.getLogger(__name__)` calls going nowhere in particular). This
phase doesn't change that — it structures what already goes to stdout as JSON, and lets Promtail
(which tails container stdout via the Docker socket) ship it to Loki. No service makes a network
call to log anything; if Loki is down, logging keeps working exactly as before, just unaggregated.

**Loki + Promtail, not a heavier stack (ELK)**: Loki pairs directly with the Grafana this project
already runs for metrics — the same reasoning `docs/observability.md`'s original Roadmap entry
already gave ("single pane of glass"). Promtail's Docker service discovery means zero per-service
config: it finds every container via the Docker socket and labels logs by
`compose_service` automatically, the same zero-config-per-service property cAdvisor already has for
metrics (`docs/observability.md`).

## Implementation

- `app/common/logging.py` — `setup_logging(service_name)`: configures the root logger with a
  hand-rolled JSON formatter (timestamp, level, service, logger name, message, optional
  `request_id`/exception traceback). Call once at process startup.
- `app/common/request_logging.py` — `add_request_logging(app, service_name)`: a FastAPI middleware
  logging one JSON line per request (method/path/status/duration), generating/propagating an
  `X-Request-ID` header for correlation.
- Wired into `app/api/main.py`, `app/analytics/main.py`, `app/extraction/main.py` (both
  `setup_logging` and `add_request_logging`), and `app/frontend/streamlit_app.py` (`setup_logging`
  only — Streamlit isn't a FastAPI app, no request middleware to add).
- `docker/Dockerfile.{api,analytics,extraction,frontend}`: each now `COPY`s `app/common/` (and
  `app/__init__.py`, where not already present) alongside the files they already copied — no other
  service needed any new dependency.
- `loki` (official image, filesystem storage, no clustering — `docker/loki/loki-config.yml`) +
  `promtail` (official image, Docker-socket service discovery —
  `docker/promtail/promtail-config.yml`), both new `docker-compose.yml` services.
  `docker/grafana/provisioning/datasources/loki.yml` adds Loki as Grafana's second datasource.

## Verification & Results

- **Real structured logs confirmed landing in Loki**, not assumed from the wiring alone: triggered
  real `GET /health`/`/metrics` calls against `api`, waited for Promtail's 5s scrape interval, then
  queried Loki directly (`/loki/api/v1/query_range` with `{compose_service="api"}`) and got back
  the exact real JSON lines `app/common/request_logging.py` emits, e.g.
  `{"timestamp": "...", "level": "INFO", "service": "api", "logger": "request", "message": "GET
  /health -> 200 (0.66ms)", "request_id": "..."}`.
- **Confirmed through Grafana's own datasource API**, not just directly against Loki (same
  verification standard `docs/observability.md` already used for Prometheus): proxied the
  identical query through `/api/datasources/proxy/uid/<loki-uid>/loki/api/v1/query_range` and got
  the same real log lines back.
- **Grafana datasource list confirms both Prometheus and Loki are provisioned** (`GET
  /api/datasources` returns both, Loki not marked default — Prometheus stays the default, unchanged
  from before this phase).
- Full project test suite re-run after wiring this in: no regressions (192 passing tests
  unaffected; the 3 pre-existing local-dev-venv DB-schema failures in
  `tests/extraction/test_cv_routes_db.py` are unrelated environment drift, not caused by this
  change).

## Known Limitations

- **Batch jobs are not wired in yet** (`app/evaluation/*`, `app/graph/*`, `app/embeddings/*`,
  `app/ingestion/*`) — they still use their existing `print()`-based output. The module would work
  there unchanged; just not done in this phase, since `docs/observability.md`'s original gap was
  specifically about the always-on services having zero log aggregation.
- **No log retention/rotation policy configured on Loki** — filesystem storage, unbounded growth
  at this project's scale. Fine for a local demo, would need a real retention policy
  (`limits_config.retention_period` + a compactor) before any real deployment.
- **No alerting on logs** (e.g. "page on N errors/minute") — same gap `docs/observability.md`
  already notes for metrics (no Alertmanager); logs inherit the same limitation.
- **ID verification/correlation across services is log-only, not distributed tracing** —
  `X-Request-ID` is generated/logged per request but not yet propagated by the Frontend or Gateway
  into the headers of the downstream calls they make, so correlating a single user action across
  `frontend -> gateway -> api` by request id isn't possible yet, only within one service's own
  logs.

## Roadmap

- Propagate `X-Request-ID` from the Gateway/Frontend into downstream calls, so a single user
  action can be traced across all three services by one id.
- Extend `setup_logging`/structured output to the batch jobs, once there's a concrete need to
  search/correlate their output rather than just read one job's stdout after running it.
- A real log retention policy before any deployment beyond this local demo.

## Related Documents

- `docs/observability.md` — the metrics side of observability (Prometheus/cAdvisor/Grafana); this
  doc is the logging counterpart
- `docs/ingress-layer.md` — the Gateway/Reverse Proxy/Load Balancer this logging now also covers
- `docs/sso.md` — the Frontend login gate this logging now also covers
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
