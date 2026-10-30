"""Basic in-app rate limiting (Phase 18) — see app/api/rate_limit.py for the
full rationale; duplicated per service by the same deliberate design.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

DEFAULT_RATE_LIMIT = "100/minute"


def rate_limit_key(request: Request) -> str:
    return request.headers.get("X-API-Key") or get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key, default_limits=[DEFAULT_RATE_LIMIT])
