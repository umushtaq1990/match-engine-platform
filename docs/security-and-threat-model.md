# Security & Threat Model

Status: v0.3 — covers API authN/authZ, rate limiting (including a real measured fix), secrets
handling, input handling, and dependency/image scanning. Security-relevant gaps found elsewhere are
cross-referenced, not duplicated — see `docs/production-readiness.md` for the consolidated view.

## Purpose & Scope

This is a local-first, single-user portfolio project, not a deployed multi-tenant service. The
threat model below is scoped accordingly — it documents what's protected against, what isn't, and
exactly what would need to change before any of that stops being true.

## Architecture & Design

### AuthN/authZ

A single static API key, not per-user authentication: there is no user model, no roles, and nothing
in the current schema (candidates/jobs/skills) represents "who is calling the API" — building
OAuth2/JWT now would add a token issuer and identity model for a user concept that doesn't exist
yet. This is the honest MVP for a single-tenant project's auth requirement, not a placeholder
nobody thought about.

The behavioral-analytics service (`app/analytics/`) uses the identical pattern — same `X-API-Key`
header, same `require_api_key` dependency (necessarily duplicated code, not a shared import, per
its own service-boundary decision — see `docs/behavioral-analytics.md`) — and reuses the *same*
`JME_API_KEY` value rather than minting a second one. Deliberate for v1: both services are
internal-only today, the same trust boundary. A distinct per-service key becomes worth the
operational overhead once that stops being true (e.g., a real frontend calling the analytics API
directly, or the two services getting different exposure).

### Rate limiting

Basic in-app rate limiting across all three live services (`api`, `analytics`, `extraction`) via
`slowapi`/`limits`, keyed by the caller's `X-API-Key` (falling back to source IP), default 100
requests/minute. `/health` and `/readiness` are explicitly exempt — infrastructure health checks
shouldn't compete with the limit real callers are rate-limited by. `analytics`'s ingestion routes
(`/events`, `/events/batch`) are also explicitly exempt, not rate-limited at all: they're internal,
best-effort instrumentation traffic driven 1:1 by matching-API request volume, not external caller
behavior — a real soak test (`docs/production-readiness.md`) measured ~2,280 instrumentation
calls/minute under moderate matching-API load, which any numeric limit here would have silently
dropped most of (this endpoint doesn't raise on failure by the caller's own design, see
`app/api/analytics_client.py`).

### Secrets handling

`docker-compose.yml` contains no credential values — Postgres user/password/db, the MLflow backend
DB name, and `JME_API_KEY` are all `${VAR}` references populated from `.env` (git-ignored;
`.env.example` is the committed template). The compose file itself can be published without leaking
a secret. The values in `.env` are still local-dev placeholders, not rotated/managed secrets —
acceptable only because nothing here leaves localhost (assumptions A7, A9 in
`docs/assumptions-register.md`).

### Input handling

- Path parameters (`candidate_id`, `job_id`) are typed `int` by FastAPI/Pydantic — malformed input
  is rejected with a 422 before reaching any matching logic or SQL.
- All DB access goes through SQLAlchemy's query builder (parameterized), never raw string-built
  SQL with request data interpolated in — the one place this project does use raw SQL
  (`truncate_all()` in `generate_synthetic_data.py`) takes no request input and isn't part of the
  API surface at all.
- No request bodies are accepted on GET-only matching endpoints, so no JSON payload validation
  surface exists to harden beyond what Pydantic already does there (the extraction and analytics
  services' POST endpoints do validate request bodies via Pydantic schemas).

### Dependency/image scanning

- **Image scanning**: `.github/workflows/ci.yml`'s `build-and-scan` job builds every service image
  and scans each with Trivy (`aquasecurity/trivy-action`, severity CRITICAL+HIGH). Each Dockerfile
  already only installs the narrow `requirements-*.txt` its own image needs (a deliberate
  image-separation decision — see `docs/adr/0001-decouple-ingestion-migrate-api.md`), which limits
  scan surface — a side effect of that design, not a substitute for actually scanning.
- **Dependency scanning**: `.github/dependabot.yml` runs weekly against the `pip` (`app/`),
  `docker`, and `github-actions` ecosystems.

## Implementation

- `app/api/deps.py`, `app/analytics/deps.py`, `app/extraction/deps.py` — the duplicated
  `require_api_key` dependency, `secrets.compare_digest` for a timing-safe comparison.
- `app/api/rate_limit.py`, `app/analytics/rate_limit.py`, `app/extraction/rate_limit.py` — `slowapi`
  limiters, Redis-backed for `api`/`analytics`, in-memory for single-worker `extraction`.
- `.github/workflows/ci.yml`'s `build-and-scan` job, `.github/dependabot.yml`.

## Verification & Results

**A real limitation found by testing, then fixed.** `api` runs 4 `uvicorn` worker processes (a
measured fix for CPU-bound scoring — see `docs/production-readiness.md`). Each worker kept its own
independent in-memory rate-limit counter — the same class of bug found separately with Prometheus
metrics under multiple workers. A real test (150 rapid sequential requests with one key) let **122**
through before 429s started, not a clean 100 — the effective limit was inflated and unevenly
enforced across workers, not a true global limit. **Fixed** by pointing `api` and `analytics` at a
shared Redis-backed `storage_uri` (`app/api/rate_limit.py`, `docs/redis-infrastructure.md`) —
re-running the identical 150-request test against the fix now measures a clean **100 through, 50
blocked**. `extraction` still runs a single worker and was left on in-memory storage; `api`'s old
imprecise-but-bounded behavior is gone, not just documented as acceptable.

## Known Limitations

**What the API-key model does NOT protect against**:
- A single leaked key grants full access to every endpoint — no scoping, no per-caller
  differentiation, no revocation short of rotating the one key.
- No TLS termination at the API itself — assumes a reverse proxy / ingress would add TLS before
  any non-localhost exposure (not built, since there is no such exposure yet).
- No audit log of who called what, when.

**Other known gaps**:
- No OAuth2/JWT, no per-caller identity — acceptable now (assumption A9), revisit before any shared
  deployment.
- No TLS (local-only, no reverse proxy yet).
- No secrets manager — env-var placeholders only (assumption A7).
- Dependency/image scanning (Trivy) currently runs report-only (`exit-code: 0`). No findings have
  been triaged into a known-acceptable baseline yet, so failing the build on every CRITICAL finding
  today would likely fail on base-image CVEs nobody has looked at, not real newly-introduced risk.

## Roadmap

- Turning Trivy into an actual blocking gate (e.g. fail on CRITICAL), once there's a triaged
  known-acceptable baseline to gate against.
- A real secrets manager and removing the API key's dev fallback entirely (fail closed if
  `JME_API_KEY` is unset, rather than defaulting to `dev-local-key-change-me`), before any shared
  or deployed environment.
- OAuth2/JWT with a real identity model, if this project ever needs multi-tenant access.

## Related Documents

- [`docs/redis-infrastructure.md`](redis-infrastructure.md) — the Redis-backed rate-limiting fix in
  full.
- [`docs/production-readiness.md`](production-readiness.md) — the consolidated production-gap view
  this document feeds into.
- [`docs/assumptions-register.md`](assumptions-register.md) — assumptions A7 (secrets), A9 (auth).
- [`docs/adr/0001-decouple-ingestion-migrate-api.md`](adr/0001-decouple-ingestion-migrate-api.md) —
  the service-separation decision that narrows each image's scan surface.
- [`docs/architecture.md`](architecture.md) — system-wide overview.
- [`docs/progress.md`](progress.md) — full build history.
