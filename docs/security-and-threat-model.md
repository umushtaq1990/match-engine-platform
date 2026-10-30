# Security & Threat Model (Phase 6, updated Phase 8)

Status: v0.2 — covers API authN/authZ (Phase 6) and dependency/image scanning (Phase 8, added
below). Security-relevant gaps found in later phases are cross-referenced, not duplicated — see
`docs/production-readiness.md` for the consolidated view.

## Scope of this document

This is a local-first, single-user portfolio project, not a deployed multi-tenant service. The
threat model below is scoped accordingly — it documents what's protected against, what isn't, and
exactly what would need to change before any of that stops being true.

## AuthN/authZ (what was built)

All non-health routes (`/candidates/.../job-matches`, `/jobs/.../candidate-matches`,
`/candidates/.../course-recommendations`) require a static API key via the `X-API-Key` header,
checked with `secrets.compare_digest` (timing-safe comparison) in `app/api/deps.py`. `/health` and
`/readiness` stay open, deliberately — a container orchestrator's health probe shouldn't need a
credential.

The key is read from the `JME_API_KEY` environment variable, with a documented local-dev
placeholder (`dev-local-key-change-me`) if unset — the same pattern already used for the dev
Postgres credentials in `docker-compose.yml` (see assumption A7). This is a single static secret,
not per-user authentication: there is no user model, no roles, and nothing in the current schema
(candidates/jobs/skills) represents "who is calling the API" — building OAuth2/JWT now would add a
token issuer and identity model for a user concept that doesn't exist yet.

**Phase 14**: the behavioral-analytics service (`app/analytics/`) uses the identical pattern —
same `X-API-Key` header, same `require_api_key` dependency (necessarily duplicated code, not a
shared import, per its own service-boundary decision — see `docs/behavioral-analytics.md`) — and
reuses the *same* `JME_API_KEY` value rather than minting a second one. Deliberate for v1: both
services are internal-only today, the same trust boundary. A distinct per-service key becomes
worth the operational overhead once that stops being true (e.g., a real frontend calling the
analytics API directly, or the two services getting different exposure).

**What this protects against**: casual/accidental access to matching endpoints (e.g., a stray
`curl` from someone on the same network without the key).

**What this does NOT protect against**:
- A single leaked key grants full access to every endpoint — no scoping, no per-caller
  differentiation, no revocation short of rotating the one key.
- No TLS termination at the API itself — assumes a reverse proxy / ingress would add TLS before
  any non-localhost exposure (not built, since there is no such exposure yet).
- No audit log of who called what, when.

## Rate limiting (Phase 18)

Basic in-app rate limiting added to all three live services (`api`, `analytics`, `extraction`) via
`slowapi`/`limits` — in-memory counters, keyed by the caller's `X-API-Key` (falling back to source
IP), default 100 requests/minute. `/health` and `/readiness` are explicitly exempt — infrastructure
health checks shouldn't compete with the limit real callers are rate-limited by.

**A real limitation found by testing, not assumed away**: `api` runs 4 `uvicorn` worker processes
(Phase 13's fix for CPU-bound scoring). Each worker keeps its own independent in-memory counter —
the same class of bug Phase 13 found with Prometheus metrics under multiple workers. A real test
(150 rapid sequential requests with one key) let **122** through before 429s started, not a clean
100 — the effective limit is inflated and unevenly enforced across workers, not a true global
limit. A correct fix needs a shared counter store (e.g. Redis) — this is the first concrete,
*measured* case in this project where that tool would provide real value, not a hypothetical one
(see `docs/production-readiness.md`). Not fixed in v1: `analytics` and `extraction` run a single
worker each, so this specific issue doesn't affect them, and `api`'s limit — while imprecise — still
meaningfully bounds abuse, just not to the exact configured number.

**`analytics`'s ingestion routes (`/events`, `/events/batch`) are explicitly exempt**, not
rate-limited at all: they're internal, best-effort instrumentation traffic driven 1:1 by
matching-API request volume, not external caller behavior. A real soak test (`docs/
production-readiness.md`) measured ~2,280 instrumentation calls/minute under moderate matching-API
load — any numeric limit here would have silently dropped most events (this endpoint doesn't raise
on failure by the caller's own design, see `app/api/analytics_client.py`), discovered by reasoning
through the actual measured soak-test throughput before shipping, not after.

## Secrets handling

- **Updated in Phase 7**: `docker-compose.yml` no longer contains any credential values — Postgres
  user/password/db, the MLflow backend DB name, and `JME_API_KEY` are all `${VAR}` references
  populated from `.env` (git-ignored; `.env.example` is the committed template). The compose file
  itself can be published without leaking a secret.
- The values in `.env` are still local-dev placeholders, not rotated/managed secrets — acceptable
  only because nothing here leaves localhost (A7, A9 in `docs/assumptions-register.md`).
- Before any shared or deployed environment: replace the placeholder values with real secrets from
  a secrets manager, and remove the API key's dev fallback entirely (fail closed if `JME_API_KEY`
  is unset, rather than defaulting to `dev-local-key-change-me`).

## Input handling

- Path parameters (`candidate_id`, `job_id`) are typed `int` by FastAPI/Pydantic — malformed input
  is rejected with a 422 before reaching any matching logic or SQL.
- All DB access goes through SQLAlchemy's query builder (parameterized), never raw string-built
  SQL with request data interpolated in — the one place this project does use raw SQL
  (`truncate_all()` in `generate_synthetic_data.py`) takes no request input and isn't part of the
  API surface at all.
- No request bodies are accepted yet (all current endpoints are GET with path/query params only),
  so no JSON payload validation surface exists to harden beyond what Pydantic already does.

## Dependency/image scanning (Phase 8)

- **Image scanning**: `.github/workflows/ci.yml`'s `build-and-scan` job builds all 5 service
  images and scans each with Trivy (`aquasecurity/trivy-action`, severity CRITICAL+HIGH). Each
  Dockerfile already only installs the narrow `requirements-*.txt` its own image needs (Phase 2's
  decoupling work), which limits scan surface — a side effect of the image-separation design, not
  a substitute for actually scanning.
- **Dependency scanning**: `.github/dependabot.yml` runs weekly against the `pip` (`app/`),
  `docker`, and `github-actions` ecosystems.
- **Not yet a hard gate**: Trivy currently runs report-only (`exit-code: 0`). No findings have been
  triaged into a known-acceptable baseline yet, so failing the build on every CRITICAL finding
  today would likely fail on base-image CVEs nobody has looked at, not real newly-introduced risk.
  Turning this into an actual blocking gate is a documented follow-up, not a silent omission.

## Known gaps carried into later phases

- No OAuth2/JWT, no per-caller identity — acceptable now (A9), revisit before any shared
  deployment.
- Rate limiting added (Phase 18) but imprecise under `api`'s multiple worker processes — see
  "Rate limiting" above for the measured gap and why a shared store (Redis) would fix it.
- No TLS (local-only, no reverse proxy yet — Phase 7/10 portability work).
- No dependency/image vulnerability scanning yet (Phase 8).
- No secrets manager — env-var placeholders only (Phase 7+ per A7).
