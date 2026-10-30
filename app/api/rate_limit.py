"""Basic in-app rate limiting (Phase 18), closing the gap documented in
docs/security-and-threat-model.md ("no rate limiting"). In-memory counters
via slowapi/limits — correct and sufficient at this project's single-
instance scale; a distributed store (e.g. Redis) would only become
necessary behind multiple replicas sharing one limit, which doesn't exist
here (see docs/production-readiness.md). Keyed by the caller's API key when
present (the only caller identity this project has), falling back to
source IP otherwise. Deliberately duplicated per service (app/api,
app/analytics, app/extraction) rather than shared — same service-boundary
reasoning as each service's own deps.py.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

DEFAULT_RATE_LIMIT = "100/minute"


def rate_limit_key(request: Request) -> str:
    return request.headers.get("X-API-Key") or get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key, default_limits=[DEFAULT_RATE_LIMIT])
