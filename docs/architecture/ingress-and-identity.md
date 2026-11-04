# Ingress and Identity (Load Balancer, Reverse Proxy, API Gateway, SSO)

Status: v1 implemented and verified end-to-end against the real running stack.

Level-3 diagram (cropped from the master Technical Architecture diagram): [`ingress-and-identity.drawio`](ingress-and-identity.drawio).

This is everything between a real user's browser and the rest of the platform: how a
request physically gets in (Load Balancer, Reverse Proxy, API Gateway) and how a human
logs in (Keycloak SSO). Two previously separate docs (`ingress-layer.md`, `sso.md`) are
merged here because they're really one story: "how does a request, and the person
behind it, get into this system."

## Part 1 : Load Balancer, Reverse Proxy, API Gateway

### Purpose & Scope

Running local ingress chain flow (`Candidate -> Load Balancer -> Reverse Proxy -> App Frontend -> API Gateway -> {matching_api, extraction_api}`) is something you can actually curl and click through.

Out of scope, by design: TLS (no real certificate, see [security-and-threat-model.md](../security-and-threat-model.md)), JWT/auth validation at the Gateway (SSO is a separate Frontend-tier login gate, see Part 2 below, not a Gateway concern here), and a cloud-managed Load Balancer (this project avoids any real Azure dependency, assumption A1 in [assumptions-register.md](../assumptions-register.md); see [ADR 0002](../adr/0002-build-the-ingress-layer.md) for why Load Balancer is nginx too, not a cloud service, even though that's the more realistic production shape).

### Architecture & Design

Three official `nginx:1.27-alpine` containers, no custom Dockerfile, configured via mounted `:ro` config files, the same convention as `docker/prometheus/prometheus.yml`:

- Reverse Proxy (`docker/nginx-reverse-proxy/nginx.conf`) fronts `frontend` only (the TLS-termination point in a real deployment; websocket `Upgrade`/`Connection` headers are forwarded, required for Streamlit's live UI updates). Two identical replicas, `reverseproxy-1`/`-2`, not published to the host.
- Load Balancer (`docker/nginx-load-balancer/nginx.conf`) round-robins across the two Reverse Proxy replicas (nginx's default algorithm, no directive needed). It is the only publicly reachable ingress component, published at `8080`. It adds an `X-Upstream` response header so round-robin is independently verifiable rather than assumed.
- API Gateway (`docker/nginx-gateway/nginx.conf`) does path-based routing between App Frontend and the backend APIs it calls: `/matching/* -> api:8000/*`, `/extraction/* -> extraction:8002/*` (prefix stripped by the trailing slash on both `location` and `proxy_pass`, so `api`/`extraction` see identical paths to before, no changes needed in those services). Not meant to be public-facing, but published at `8099` anyway for direct debugging, matching this repo's existing habit of publishing every port.

Why only Load Balancer is published to the host: every other service in this repo publishes its port for local debugging convenience. This layer deliberately breaks that habit for Reverse Proxy/Gateway, the entire point of an ingress chain is a single public entry point; publishing every internal hop too would defeat the demonstration.

### Implementation

`docker-compose.yml`: four services (`gateway`, `reverseproxy-1`, `reverseproxy-2`, `loadbalancer`), each with a healthcheck (`wget` against `127.0.0.1`, not `localhost`, see Known Limitations). `frontend`'s `API_URL`/`EXTRACTION_URL` default to `http://gateway/matching`/`http://gateway/extraction` instead of calling `api`/`extraction` directly (`app/frontend/streamlit_app.py`), and `frontend` depends on `gateway` being healthy.

### Verification & Results

All verified against the real running containers, not assumed from config alone:

- Round-robin: 5 sequential `curl http://localhost:8080/` calls, `X-Upstream` alternated between the two `reverseproxy-1`/`reverseproxy-2` container IPs (172.21.0.10 / 172.21.0.11), confirming real load distribution, not just that nginx accepted the config.
- Gateway routing is transparent: `curl http://localhost:8099/matching/candidates/1/job-matches` returned the exact same response (real ranked match data, after running `migrate`/`seed`) as calling `api:8000` directly confirming the prefix-strip routing works and `api` is unaware it's being proxied.
- Websocket passthrough through the full chain: a raw HTTP/1.1 Upgrade handshake (`Connection: Upgrade`, `Upgrade: websocket`) sent to `http://localhost:8080/_stcore/stream` returned `101 Switching Protocols` confirms Load Balancer to Reverse Proxy to Frontend preserves the websocket upgrade end to end, not just plain HTTP GETs. Page title (`<title>Streamlit</title>`) also confirmed rendering through the full chain.
- A real bug found and fixed during verification: the `gateway`/`reverseproxy`/`loadbalancer` healthchecks initially used `http://localhost/...`, which failed with "connection refused" Alpine's `wget` resolves `localhost` to `::1` first, but nginx (as configured here) only binds `0.0.0.0:80` (IPv4), not `[::]:80`. Fixed by healthchecking `127.0.0.1` explicitly instead of `localhost`.

### Known Limitations

- No TLS ; Reverse Proxy terminates connections in plaintext. The real TLS-termination responsibility this component would have in production isn't exercised here (see [security-and-threat-model.md](../security-and-threat-model.md)).
- No JWT/auth validation at the Gateway, it's pure routing, no rate limiting or token checks of its own (the APIs' existing `JME_API_KEY` check and Redis-backed rate limiting, documented in [security-and-threat-model.md](../security-and-threat-model.md) and [data-platform/redis-infrastructure.md](data-platform/redis-infrastructure.md), are unchanged and still the only enforcement).
- Load Balancer is nginx, not a cloud-managed service, an intentional choice given this project's no-real-Azure-subscription constraint (assumption A1), not a claim that this is how a real deployment's Load Balancer would be built (see [ADR 0002](../adr/0002-build-the-ingress-layer.md)).
- Gateway/Reverse Proxy healthchecks use IPv4-only `127.0.0.1` rather than fixing nginx to also bind `[::]`/dual-stack, sufficient for this project's local-only scope.

### Roadmap

- JWT validation at the Gateway (rejecting unauthenticated requests before they reach `api`/`extraction`) once the SSO tokens below are something the Gateway could plausibly check, not built yet, see Part 2's Known Limitations.
- A real TLS certificate at the Reverse Proxy (even a self-signed one) if this project ever needs to demonstrate HTTPS locally.

## Part 2 : SSO / Identity (Keycloak)

### Purpose & Scope

`docs/architect/JME_Technical_Architecture.drawio` drew `App Frontend -> SSO` as a "documented, not provisioned" future login hop. This phase built it for real: a Keycloak container with a pre-seeded realm, and a real OIDC Authorization Code login gate in front of the demo UI.

This is a human/browser login gate only. `JME_API_KEY` (the header every API caller, including `frontend` itself, server-side, already attaches) is completely unchanged; it's still what `api`/`analytics`/`extraction` check for every request. SSO governs whether a human can see the Frontend UI at all; it does not replace or touch the existing service-to-service auth. See assumption A9 in [assumptions-register.md](../assumptions-register.md).

Out of scope, by design: JWT validation at the API Gateway (see Part 1's Roadmap), PKCE, token refresh, role-based access control, and a hardened session mechanism (see Known Limitations), all deliberately deferred for a local demo whose job is to show the login flow works, not to be production auth infrastructure.

### Architecture & Design

Keycloak in dev mode, realm pre-seeded via `--import-realm` (`docker/keycloak/jme-realm.json`), no manual admin-console setup needed to run the demo. The realm defines one public client (`jme-frontend`, no secret standard for a browser-based SPA/public client) and two seeded test users (`candidate`/`employer`, passwords in the realm file, clearly local-dev-only).

Two issuer URLs, a real nuance, not obvious until it broke: a browser redirecting to Keycloak's login page must use the host-published URL (`http://localhost:8180/realms/jme`, `OIDC_PUBLIC_ISSUER`), but the Frontend container's own server-side token exchange call must use the internal Docker network address (`http://keycloak:8080/realms/jme`, `OIDC_INTERNAL_ISSUER`) `localhost:8180` means nothing from inside a container. Both point at the same realm; which one is used depends on who's making the call (the user's browser vs. the Frontend's own backend code).

Hand-rolled with `requests`, not `authlib`: this project's existing convention for calling another service is a module-level `os.environ.get("X_URL", "http://...")` plus plain `requests` calls (`app/api/analytics_client.py`, `app/frontend/streamlit_app.py`'s existing `API_URL`/`EXTRACTION_URL`), not a framework. `authlib` (and most other OIDC client libraries) assume a traditional request/response web framework with real routes, Streamlit doesn't have that; it's a
single script re-run top-to-bottom on every interaction. A roughly 50-line hand-rolled Authorization Code flow using `st.query_params` and `requests.post` fits this project's style better and adds zero new dependencies.

### Implementation

`app/frontend/streamlit_app.py`'s `_require_login()`:

1. If `st.session_state["id_token_claims"]` already exists, return immediately (already logged in).
2. If the URL has `?code=...&state=...` (Keycloak just redirected back), verify `state` matches what was generated before redirecting (CSRF check), `POST` the code to Keycloak's token endpoint (`OIDC_INTERNAL_ISSUER`), decode the returned `id_token`'s JWT payload (no signature verification local demo only, see Known Limitations) into `session_state`, then `st.rerun()`.
3. Otherwise, generate a random `state`, build the Keycloak login URL (`OIDC_PUBLIC_ISSUER/protocol/openid-connect/auth`), show it as a "Log in with SSO" link, and `st.stop()` nothing else in the app runs until this resolves.

`docker-compose.yml`: a `keycloak` service (official `quay.io/keycloak/keycloak:26.0`, no custom Dockerfile), `frontend` depends on it being healthy and gets three new `OIDC_*` env vars. `docker/keycloak/jme-realm.json` is the entire realm definition `sslRequired: "none"` is set deliberately (see Known Limitations: no TLS anywhere in this stack yet, and Keycloak would otherwise require HTTPS for the container-to-container token-exchange call, which isn't `localhost` from Keycloak's perspective).

### Verification & Results

Verified with a real scripted session against the real running stack, not browser screenshots, but a genuine HTTP client walking the exact same steps a browser would, hitting real endpoints:

1. Wrong password is rejected by Keycloak itself, not app-side logic: submitting the real login form with an incorrect password returns Keycloak's own "Invalid username or password" page (no `code=` in any redirect).
2. Correct password succeeds end-to-end: real login form submission, real `302` redirect back to the Frontend's exact `redirect_uri` with a real authorization `code` and the original `state`, real token exchange against Keycloak's token endpoint, real `id_token` returned, decoding to `{"preferred_username": "candidate", "email": "candidate@example.com", "aud": "jme-frontend"}`.
3. The actual running Frontend container exercises this correctly: following Keycloak's real redirect back into the real `http://localhost:8080/` (through the full Load Balancer to Reverse Proxy to Frontend chain from Part 1) lands on the authenticated app, not the login gate.
4. `streamlit.testing.v1.AppTest` (10 tests, `tests/frontend/test_streamlit_app.py`): an unauthenticated session sees only the login link (with the correct `client_id`/ `response_type`/`redirect_uri` query params) and never reaches the role picker; a session with `id_token_claims` pre-seeded sees the real app and a "Log out (username)" button; clicking logout clears the session and shows the login link again. The real Authorization Code HTTP exchange itself isn't mocked in these, it's covered by points 1-3 above, against the real Keycloak.
5. A real, non-obvious finding made during verification: an HTTP client that strictly follows RFC 6265 (refusing to send a `Secure`-flagged cookie back over plain HTTP) cannot complete this flow at all against `http://localhost`, Keycloak's session cookie (`AUTH_SESSION_ID`/`KC_RESTART`) is always `Secure; SameSite=None` regardless of TLS. Real browsers, however, treat `http://localhost` specifically as a "potentially trustworthy" secure context and store/send Secure cookies there anyway (a documented browser exception for local development), confirmed by re-running the verification with a cookie-handling shim that matches real browser behavior, at which point the flow completed exactly as expected. This means the login flow only works reliably at the literal hostname `localhost` (not `127.0.0.1`, not any other hostname), worth knowing if this ever needs to run somewhere else.

### Known Limitations

- `st.session_state` is not a hardened session mechanism, it's per Streamlit server connection, not a signed/encrypted cookie-based session a real auth system would use. Acceptable for a local single-user demo, not for anything deployed (same class of gap as `JME_API_KEY`, assumption A9).
- No automated test exercises the CSRF `state`-comparison branch itself (see Verification & Results point 6), covered today only by manual real-browser verification, not by `AppTest`.
- No ID token signature verification, the JWT payload is base64-decoded and trusted as-is, without checking Keycloak's signature on it. Fine when the token exchange itself happened over a direct, trusted connection to Keycloak (which it does here), but a real production client should verify the signature against Keycloak's JWKS endpoint.
- No PKCE recommended even for public clients in current OIDC best practice; not added here to keep the flow's line count small for a local demo with no real attacker model.
- No TLS anywhere (`sslRequired: "none"` on the realm), see Part 1 and [security-and-threat-model.md](../security-and-threat-model.md). This also means the login flow only works at `http://localhost` specifically (see Verification point 5), would need revisiting entirely if TLS is ever added.
- No JWT validation at the API Gateway, a logged-in Frontend session doesn't currently cause the Gateway to check anything on the API calls it forwards; those are still only protected by `JME_API_KEY`. See Part 1's Roadmap.

### Roadmap

- Real signature verification against Keycloak's JWKS endpoint.
- PKCE, if this ever needs to defend against a more realistic threat model.
- JWT validation at the API Gateway, once there's a reason the Gateway itself needs to enforce authentication rather than just route.

## Resource Footprint and Scaling

All three nginx containers and Keycloak are lightweight relative to everything behind them `docker stats` shows nginx idling at single-digit MB each (see [operations/infrastructure-sizing.md](../operations/infrastructure-sizing.md) for the full per-service table). nginx is also stateless request routing with no per-request compute of its own, so this layer is not where concurrent-user capacity is actually won or lost: the real ceiling is whatever sits behind the Gateway (see [application-services/README.md](application-services/README.md) and [ai-ml-and-mlops.md](ai-ml-and-mlops.md)). Scaling this layer, if it were ever needed, is the standard nginx pattern: add more Reverse Proxy replicas behind the Load Balancer (already 2 today) and/or more Gateway instances behind a second small load balancer, nothing architecturally new, unlike the application/AI layers below. Keycloak in dev mode is single-instance with no HA story; a real deployment would run it clustered against a shared database, not built here (local-only scope, same as every other single-instance service in this project).

See [operations/capacity-planning.md](../operations/capacity-planning.md) for the full worked concurrent-user scenario across every layer.

## Related Documents

- [adr/0002-build-the-ingress-layer.md](../adr/0002-build-the-ingress-layer.md) : why these three components exist and why nginx
- [frontend.md](frontend.md) : the UI this gate sits in front of, and how it's reached (`:8080`, not `:8501` directly)
- [security-and-threat-model.md](../security-and-threat-model.md) — overall auth/TLS posture
- [assumptions-register.md](../assumptions-register.md) : assumption A1 (no real Azure subscription), assumption A9 (the separate, unchanged `JME_API_KEY` mechanism)
- [architecture.md](../architecture.md) : overall system architecture
- `docs/progress.md` : full build history, kept locally as an internal working log, not included in this repository
