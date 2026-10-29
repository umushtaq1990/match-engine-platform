# Security & Threat Model (Phase 6)

Status: v0.1 — covers what Phase 6 (API wiring) introduces. Dependency/image scanning is a
separate, not-yet-built Phase 8 deliverable (see `docs/requirements.md`).

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

**What this protects against**: casual/accidental access to matching endpoints (e.g., a stray
`curl` from someone on the same network without the key).

**What this does NOT protect against**:
- A single leaked key grants full access to every endpoint — no scoping, no per-caller
  differentiation, no revocation short of rotating the one key.
- No rate limiting — a holder of the valid key can call endpoints as fast as the process allows.
- No TLS termination at the API itself — assumes a reverse proxy / ingress would add TLS before
  any non-localhost exposure (not built, since there is no such exposure yet).
- No audit log of who called what, when.

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

## Dependency/image scanning

Explicitly deferred to Phase 8 per `docs/requirements.md` ("Container/dependency scanning
introduced in Phase 8, not deferred to the end") — not done here. Each Dockerfile already only
installs the narrow `requirements-*.txt` its own image needs (documented since Phase 2's
decoupling work), which limits scan surface once scanning is added, but that's a side effect of
the image-separation design, not a substitute for actually scanning.

## Known gaps carried into later phases

- No OAuth2/JWT, no per-caller identity — acceptable now (A9), revisit before any shared
  deployment.
- No rate limiting.
- No TLS (local-only, no reverse proxy yet — Phase 7/10 portability work).
- No dependency/image vulnerability scanning yet (Phase 8).
- No secrets manager — env-var placeholders only (Phase 7+ per A7).
