"""Phase 30: one JSON log line per request (method/path/status/duration),
with an X-Request-ID propagated/generated per request for correlation. See
app/common/logging.py for why this one module is shared, and docs/logging.md.
"""
import logging
import time
import uuid

from fastapi import FastAPI, Request

logger = logging.getLogger("request")


def add_request_logging(app: FastAPI, service_name: str) -> None:
    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s (%sms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={"request_id": request_id},
        )
        return response
