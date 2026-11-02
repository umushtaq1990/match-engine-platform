# SSO / Identity (Keycloak)

Status: v1 — implemented and verified end-to-end against a real running Keycloak.

## Purpose & Scope

`docs/architect/JME_Technical_Architecture.drawio` drew `App Frontend -> SSO` as a "documented, not
provisioned" future login hop. This phase builds it for real: a Keycloak container with a
pre-seeded realm, and a real OIDC Authorization Code login gate in front of the demo UI.

**This is a human/browser login gate only.** `JME_API_KEY` (the header every API caller — including
`frontend` itself, server-side — already attaches) is completely unchanged; it's still what
`api`/`analytics`/`extraction` check for every request. SSO governs whether a human can *see* the
Frontend UI at all; it does not replace or touch the existing service-to-service auth. See
assumption A9 in `docs/assumptions-register.md`.

**Out of scope, by design**: JWT validation at the API Gateway (see `docs/ingress-layer.md`'s
Roadmap), PKCE, token refresh, role-based access control, and a hardened session mechanism (see
Known Limitations) — all deliberately deferred for a local demo whose job is to show the login
flow works, not to be production auth infrastructure.

## Architecture & Design

**Keycloak in dev mode**, realm pre-seeded via `--import-realm` (`docker/keycloak/jme-realm.json`)
— no manual admin-console setup needed to run the demo. The realm defines one public client
(`jme-frontend`, no secret — standard for a browser-based SPA/public client) and two seeded test
users (`candidate`/`employer`, passwords in the realm file, clearly local-dev-only).

**Two issuer URLs — a real nuance, not obvious until it broke**: a browser redirecting to
Keycloak's login page must use the host-published URL (`http://localhost:8180/realms/jme`,
`OIDC_PUBLIC_ISSUER`), but the Frontend container's own server-side token exchange call must use
the internal Docker network address (`http://keycloak:8080/realms/jme`, `OIDC_INTERNAL_ISSUER`) —
`localhost:8180` means nothing from inside a container. Both point at the same realm; which one is
used depends on who's making the call (the user's browser vs. the Frontend's own backend code).

**Hand-rolled with `requests`, not `authlib`**: this project's existing convention for calling
another service is a module-level `os.environ.get("X_URL", "http://...")` plus plain `requests`
calls (`app/api/analytics_client.py`, `app/frontend/streamlit_app.py`'s existing `API_URL`/
`EXTRACTION_URL`), not a framework. `authlib` (and most other OIDC client libraries) assume a
traditional request/response web framework with real routes — Streamlit doesn't have that; it's a
single script re-run top-to-bottom on every interaction. A ~50-line hand-rolled Authorization Code
flow using `st.query_params` and `requests.post` fits this project's style better and adds zero new
dependencies.

## Implementation

`app/frontend/streamlit_app.py`'s `_require_login()`:
1. If `st.session_state["id_token_claims"]` already exists, return immediately (already logged in).
2. If the URL has `?code=...&state=...` (Keycloak just redirected back), verify `state` matches
   what was generated before redirecting (CSRF check), `POST` the code to Keycloak's token
   endpoint (`OIDC_INTERNAL_ISSUER`), decode the returned `id_token`'s JWT payload (no signature
   verification — local demo only, see Known Limitations) into `session_state`, then `st.rerun()`.
3. Otherwise, generate a random `state`, build the Keycloak login URL
   (`OIDC_PUBLIC_ISSUER/protocol/openid-connect/auth`), show it as a "Log in with SSO" link, and
   `st.stop()` — nothing else in the app runs until this resolves.

`docker-compose.yml`: new `keycloak` service (official `quay.io/keycloak/keycloak:26.0`, no custom
Dockerfile), `frontend` now depends on it being healthy and gets three new `OIDC_*` env vars.
`docker/keycloak/jme-realm.json` is the entire realm definition — `sslRequired: "none"` is set
deliberately (see Known Limitations: no TLS anywhere in this stack yet, and Keycloak would
otherwise require HTTPS for the container-to-container token-exchange call, which isn't
`localhost` from Keycloak's perspective).

## Verification & Results

Verified with a real scripted session against the real running stack — not browser screenshots,
but a genuine HTTP client walking the exact same steps a browser would, hitting real endpoints:

1. **Wrong password is rejected by Keycloak itself**, not app-side logic: submitting the real
   login form with an incorrect password returns Keycloak's own "Invalid username or password"
   page (no `code=` in any redirect).
2. **Correct password succeeds end-to-end**: real login form submission → real `302` redirect
   back to the Frontend's exact `redirect_uri` with a real authorization `code` and the original
   `state` → real token exchange against Keycloak's token endpoint → real `id_token` returned,
   decoding to `{"preferred_username": "candidate", "email": "candidate@example.com", "aud":
   "jme-frontend"}`.
3. **The actual running Frontend container exercises this correctly**: following Keycloak's real
   redirect back into the real `http://localhost:8080/` (through the full Load Balancer → Reverse
   Proxy → Frontend chain from Phase 28) lands on the authenticated app, not the login gate.
4. **`streamlit.testing.v1.AppTest`** (10 tests, `tests/frontend/test_streamlit_app.py`): an
   unauthenticated session sees only the login link (with the correct `client_id`/
   `response_type`/`redirect_uri` query params) and never reaches the role picker; a session with
   `id_token_claims` pre-seeded sees the real app and a "Log out (username)" button; clicking
   logout clears the session and shows the login link again. The real Authorization Code HTTP
   exchange itself isn't mocked in these — it's covered by point 1-3 above, against the real
   Keycloak.
5. **A real, non-obvious finding made during verification**: an HTTP client that strictly follows
   RFC 6265 (refusing to send a `Secure`-flagged cookie back over plain HTTP) cannot complete this
   flow at all against `http://localhost` — Keycloak's session cookie (`AUTH_SESSION_ID`/
   `KC_RESTART`) is always `Secure; SameSite=None` regardless of TLS. Real browsers, however, treat
   `http://localhost` specifically as a "potentially trustworthy" secure context and store/send
   Secure cookies there anyway (a documented browser exception for local development) — confirmed
   by re-running the verification with a cookie-handling shim that matches real browser behavior,
   at which point the flow completed exactly as expected. **This means the login flow only works
   reliably at the literal hostname `localhost`** (not `127.0.0.1`, not any other hostname) — worth
   knowing if this ever needs to run somewhere else.

## Known Limitations

- **`st.session_state` is not a hardened session mechanism** — it's per Streamlit server
  connection, not a signed/encrypted cookie-based session a real auth system would use. Acceptable
  for a local single-user demo, not for anything deployed (same class of gap as `JME_API_KEY`,
  assumption A9).
- **No ID token signature verification** — the JWT payload is base64-decoded and trusted as-is,
  without checking Keycloak's signature on it. Fine when the token exchange itself happened over a
  direct, trusted connection to Keycloak (which it does here), but a real production client should
  verify the signature against Keycloak's JWKS endpoint.
- **No PKCE** — recommended even for public clients in current OIDC best practice; not added here
  to keep the flow's line count small for a local demo with no real attacker model.
- **No TLS anywhere** (`sslRequired: "none"` on the realm) — see `docs/ingress-layer.md`/
  `docs/security-and-threat-model.md`. This also means the login flow only works at
  `http://localhost` specifically (see Verification point 5) — would need revisiting entirely if
  TLS is ever added.
- **No JWT validation at the API Gateway** — a logged-in Frontend session doesn't currently cause
  the Gateway to check anything on the API calls it forwards; those are still only protected by
  `JME_API_KEY`. See `docs/ingress-layer.md`'s Roadmap.

## Roadmap

- Real signature verification against Keycloak's JWKS endpoint.
- PKCE, if this ever needs to defend against a more realistic threat model.
- JWT validation at the API Gateway (Phase 28), once there's a reason the Gateway itself needs to
  enforce authentication rather than just route.

## Related Documents

- `docs/ingress-layer.md` — the Load Balancer/Reverse Proxy/Gateway chain this login flow runs
  through
- `docs/frontend.md` — the UI this gate sits in front of
- `docs/assumptions-register.md` — assumption A9 (the separate, unchanged `JME_API_KEY` mechanism)
- `docs/security-and-threat-model.md` — overall auth/TLS posture
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
