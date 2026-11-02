# Ingress Layer (Load Balancer, Reverse Proxy, API Gateway)

Status: v1 — implemented and verified end-to-end against the real running stack.

## Purpose & Scope

`docs/architect/JME_Technical_Architecture.drawio` originally drew this layer as "documented, not
provisioned" — plausible future boxes, not real containers. This phase replaces that with a real,
running local ingress chain so the diagram's flow (`Candidate -> Load Balancer -> Reverse Proxy ->
App Frontend -> API Gateway -> {matching_api, extraction_api}`) is something you can actually curl
and click through, not just read.

**Out of scope, by design**: TLS (no real certificate — see `docs/security-and-threat-model.md`),
JWT/auth validation at the Gateway (SSO is a separate Frontend-tier login gate — see
`docs/sso.md`, not a Gateway concern here), and a cloud-managed Load Balancer (this project avoids
any real Azure dependency — assumption A1 in `docs/assumptions-register.md`; see ADR 0002 for why
Load Balancer is nginx too, not a cloud service, even though that's the more realistic production
shape).

## Architecture & Design

Three official `nginx:1.27-alpine` containers, no custom Dockerfile — configured via mounted `:ro`
config files, the same convention as `docker/prometheus/prometheus.yml`:

- **Reverse Proxy** (`docker/nginx-reverse-proxy/nginx.conf`) — fronts `frontend` only (TLS
  termination point in a real deployment; websocket `Upgrade`/`Connection` headers forwarded,
  required for Streamlit's live UI updates). Two identical replicas, `reverseproxy-1`/`-2`, not
  published to the host.
- **Load Balancer** (`docker/nginx-load-balancer/nginx.conf`) — round-robins across the two
  Reverse Proxy replicas (nginx's default algorithm, no directive needed). The **only** publicly
  reachable ingress component, published at `8080`. Adds an `X-Upstream` response header so
  round-robin is independently verifiable rather than assumed.
- **API Gateway** (`docker/nginx-gateway/nginx.conf`) — path-based routing between `App Frontend`
  and the backend APIs it calls: `/matching/* -> api:8000/*`, `/extraction/* -> extraction:8002/*`
  (prefix stripped by the trailing slash on both `location` and `proxy_pass`, so `api`/`extraction`
  see identical paths to before — no changes needed in those services). Not meant to be
  public-facing, but published at `8099` anyway for direct debugging, matching this repo's existing
  habit of publishing every port.

**Why only Load Balancer is published to the host**: every other service in this repo publishes its
port for local debugging convenience. This layer deliberately breaks that habit for Reverse
Proxy/Gateway — the entire point of an ingress chain is a single public entry point; publishing
every internal hop too would defeat the demonstration.

## Implementation

`docker-compose.yml`: four new services (`gateway`, `reverseproxy-1`, `reverseproxy-2`,
`loadbalancer`), each with a healthcheck (`wget` against `127.0.0.1` — not `localhost`, see Known
Limitations). `frontend`'s `API_URL`/`EXTRACTION_URL` now default to
`http://gateway/matching`/`http://gateway/extraction` instead of calling `api`/`extraction`
directly (`app/frontend/streamlit_app.py`), and `frontend` now also depends on `gateway` being
healthy.

## Verification & Results

All verified against the real running containers, not assumed from config alone:

- **Round-robin**: 5 sequential `curl http://localhost:8080/` calls — `X-Upstream` alternated
  between the two `reverseproxy-1`/`reverseproxy-2` container IPs (172.21.0.10 / 172.21.0.11),
  confirming real load distribution, not just that nginx accepted the config.
- **Gateway routing is transparent**: `curl http://localhost:8099/matching/candidates/1/job-matches`
  returned the exact same response (real ranked match data, after running `migrate`/`seed`) as
  calling `api:8000` directly — confirming the prefix-strip routing works and `api` is unaware it's
  being proxied.
- **Websocket passthrough through the full chain**: a raw HTTP/1.1 Upgrade handshake
  (`Connection: Upgrade`, `Upgrade: websocket`) sent to `http://localhost:8080/_stcore/stream`
  returned `101 Switching Protocols` — confirms Load Balancer -> Reverse Proxy -> Frontend
  preserves the websocket upgrade end-to-end, not just plain HTTP GETs. Page title (`<title>
  Streamlit</title>`) also confirmed rendering through the full chain.
- **A real bug found and fixed during verification**: the `gateway`/`reverseproxy`/`loadbalancer`
  healthchecks initially used `http://localhost/...`, which failed with "connection refused" —
  Alpine's `wget` resolves `localhost` to `::1` first, but nginx (as configured here) only binds
  `0.0.0.0:80` (IPv4), not `[::]:80`. Fixed by healthchecking `127.0.0.1` explicitly instead of
  `localhost`.

## Known Limitations

- No TLS — Reverse Proxy terminates connections in plaintext. The real TLS-termination
  responsibility this component would have in production isn't exercised here (see
  `docs/security-and-threat-model.md`).
- No JWT/auth validation at the Gateway — it's pure routing, no rate limiting or token checks of
  its own (the APIs' existing `JME_API_KEY` check and Redis-backed rate limiting, documented in
  `docs/security-and-threat-model.md`/`docs/redis-infrastructure.md`, are unchanged and still the
  only enforcement).
- Load Balancer is nginx, not a cloud-managed service — an intentional choice given this project's
  no-real-Azure-subscription constraint (assumption A1), not a claim that this is how a real
  deployment's Load Balancer would be built (see ADR 0002).
- Gateway/Reverse Proxy healthchecks use IPv4-only `127.0.0.1` rather than fixing nginx to also
  bind `[::]`/dual-stack — sufficient for this project's local-only scope.

## Roadmap

- JWT validation at the Gateway (rejecting unauthenticated requests before they reach `api`/
  `extraction`) once Phase 29's SSO tokens are something the Gateway could plausibly check — not
  built in Phase 29 either, see `docs/sso.md`'s Known Limitations.
- A real TLS certificate at the Reverse Proxy (even a self-signed one) if this project ever needs
  to demonstrate HTTPS locally.

## Related Documents

- `docs/adr/0002-build-the-ingress-layer.md` — why these three components exist and why nginx
- `docs/sso.md` — the separate Frontend-tier login gate (Phase 29)
- `docs/frontend.md` — how the demo UI is now reached (`:8080`, not `:8501` directly)
- `docs/security-and-threat-model.md` — TLS/auth gaps this layer does and doesn't close
- `docs/assumptions-register.md` — assumption A1 (no real Azure subscription)
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
