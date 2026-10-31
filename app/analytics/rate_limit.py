"""Basic in-app rate limiting (Phase 18) — see app/api/rate_limit.py for the
full rationale and the Phase 21 Redis-backed storage decision; duplicated
per service by the same deliberate design.
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
