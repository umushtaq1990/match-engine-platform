# ADR 0002: Build a real local ingress layer (Load Balancer, Reverse Proxy, API Gateway) instead of leaving it diagram-only

Status: Accepted (2026-11-02)

## Problem

`docs/architect/JME_Technical_Architecture.drawio` drew a plausible ingress layer in front of
`App Frontend` (Load Balancer, Reverse Proxy, API Gateway, SSO/ID) — deliberately styled
dashed-gray, "documented, not provisioned," to be honest that none of it was real. Once the
diagram's flow was worked out to be logically correct (`Candidate -> Load Balancer -> Reverse
Proxy -> App Frontend -> API Gateway -> {matching_api, extraction_api}`), the natural next
question was whether to actually build it, or leave the project with a gap between "what the
diagram claims" and "what runs."

## Alternatives considered

1. **Leave it diagram-only.** Zero additional work, but the diagram would keep asserting a flow
   nothing in the repo demonstrates — the project's own stated discipline ("every claim is
   measured, not assumed") argues against this once the flow is this well-specified.
2. **Kong or Traefik for the API Gateway.** Real API gateways with plugin ecosystems (rate
   limiting, auth plugins). Rejected for now: this project's existing rate limiting
   (`app/api/rate_limit.py`, Redis-backed, documented in `docs/redis-infrastructure.md`) and
   auth (`JME_API_KEY`) already live in each service; a gateway-level plugin system would
   duplicate enforcement that already exists elsewhere, for no verified gain at this project's
   scale. Revisit only if JWT validation actually needs to move to the edge (see
   `docs/ingress-layer.md`'s Roadmap).
3. **Cloud-managed Load Balancer (Azure Load Balancer / Application Gateway).** The realistic
   production shape (and what `k8s/overlays/azure`'s Ingress already assumes, per
   `docs/kubernetes.md`) — but this project has an explicit, standing decision never to use a real
   Azure subscription (assumption A1, `docs/assumptions-register.md`). An nginx container
   round-robining across Reverse Proxy replicas is the honest local equivalent: it demonstrates
   the *pattern* (a single public entry point load-balancing across backend replicas) without
   claiming to be what a real cloud deployment would use.
4. **nginx for all three components, plain config, no plugin ecosystem.** Chosen — simplest
   thing that actually routes and balances real traffic, consistent with this project's general
   bias (see ADR 0001) toward the smallest tool that closes a real, verified gap rather than the
   most "production-realistic" one.

## Decision

- `reverseproxy-1`/`reverseproxy-2` (nginx, identical config) front `App Frontend` only —
  websocket-aware (`Upgrade`/`Connection` headers), since Streamlit depends on a live websocket
  connection.
- `loadbalancer` (nginx) round-robins across the two Reverse Proxy replicas and is the *only*
  publicly-reachable ingress component — a deliberate deviation from this repo's habit of
  publishing every service's port, since a real ingress chain only has one public entry point.
- `gateway` (nginx) sits between `App Frontend` and the backend APIs it calls
  (`matching_api`/`extraction_api`), routing by path prefix (stripped before forwarding, so those
  services see identical paths to before — no changes needed there).
- `app/frontend/streamlit_app.py`'s `API_URL`/`EXTRACTION_URL` now point at `gateway`, not
  directly at `api`/`extraction` — this is what makes it a real request path, not decorative
  containers sitting unused next to the real traffic.
- No JWT/auth logic added at the Gateway in this phase — out of scope, see
  `docs/ingress-layer.md`'s Known Limitations/Roadmap.

## Consequences

- Verified: round-robin across Reverse Proxy replicas (`X-Upstream` header alternates across
  real requests), transparent Gateway routing (identical responses via the Gateway vs. calling
  `api` directly), and a full websocket upgrade handshake succeeding end-to-end through
  `Load Balancer -> Reverse Proxy -> Frontend` (`101 Switching Protocols`) — see
  `docs/ingress-layer.md`'s Verification & Results.
- The demo's public entry point changes from `http://localhost:8501` (Phase 27) to
  `http://localhost:8080` (this phase) — `docs/frontend.md` updated accordingly; `:8501` stays
  published for direct debugging only.
- `docs/security-and-threat-model.md` updated: a real reverse proxy exists now, but with no TLS
  certificate — don't overclaim that TLS is solved.
- A real bug was found and fixed while verifying this: nginx's healthchecks using `localhost`
  failed because Alpine's `wget` resolves `::1` first while nginx (as configured) only binds
  `0.0.0.0:80` — fixed by healthchecking `127.0.0.1` explicitly.

## Reconsider when

- If JWT/token validation needs to happen before a request reaches `api`/`extraction` (once
  Phase 29's SSO exists), revisit whether plain nginx is still sufficient or whether an
  auth-aware gateway (Kong/Traefik/OpenResty+Lua) becomes worth the added complexity.
- If this project ever needs a real cloud deployment story beyond the already-stale/documented-only
  `k8s/overlays/azure` (see `docs/kubernetes.md`'s Known Limitations), Load Balancer should become
  a real Azure Load Balancer / Application Gateway, not nginx — this ADR's choice is a local-demo
  stand-in, not a recommendation for production.

## Related Documents

- `docs/ingress-layer.md` — full implementation and verification detail
- `docs/adr/0001-decouple-ingestion-migrate-api.md` — this project's general service-boundary
  pattern (small, purpose-built images over heavier general-purpose tools)
- `docs/assumptions-register.md` — assumption A1 (no real Azure subscription)
- `docs/kubernetes.md` — the Azure overlay's own (untested) Ingress/Application Gateway story
