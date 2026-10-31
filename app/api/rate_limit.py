"""Basic in-app rate limiting (Phase 18), closing the gap documented in
docs/security-and-threat-model.md ("no rate limiting"). Keyed by the
caller's API key when present (the only caller identity this project has),
falling back to source IP otherwise. Deliberately duplicated per service
(app/api, app/analytics, app/extraction) rather than shared — same
service-boundary reasoning as each service's own deps.py.

Storage backend (Phase 21): Phase 18's own load test found `slowapi`'s
default in-memory storage gives each of `api`'s 4 uvicorn worker processes
an independent counter — a real test let 122 requests through before 429s,
not a clean 100 (docs/security-and-threat-model.md). When REDIS_URL is set
(docker-compose.yml, for `api`), `limits` is pointed at Redis instead, so
all worker processes share one counter. When it isn't set (bare `pytest`,
no containers), falls back to in-memory storage — a single TestClient
process never exercises the multi-worker bug this fixes, so there's nothing
Redis would change there. See docs/redis-infrastructure.md.
"""
import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

DEFAULT_RATE_LIMIT = "100/minute"

REDIS_URL = os.environ.get("REDIS_URL")


def rate_limit_key(request: Request) -> str:
    return request.headers.get("X-API-Key") or get_remote_address(request)


limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=[DEFAULT_RATE_LIMIT],
    storage_uri=REDIS_URL,
)
