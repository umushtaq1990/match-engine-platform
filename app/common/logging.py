"""Phase 30: centralized structured logging, imported by every service.

This is the one deliberate exception to this project's general
per-service-duplication convention (see app/api/rate_limit.py, each
service's own deps.py): those are duplicated to avoid coupling one image's
dependencies to another service's business logic. Logging setup has zero
business logic and zero extra dependencies (stdlib `logging` only) — it's
pure cross-cutting infra, so the coupling argument doesn't apply the same
way. This is scoped narrowly to not set a precedent for sharing anything
else.

Keeps stdout as the transport (same convention every service already used,
just unstructured before this phase) — Promtail tails container stdout and
ships to Loki, so no service makes a network call to log anything. See
docs/logging.md.
"""
import datetime
import json
import logging
import sys


class _JSONFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        request_id = getattr(record, "request_id", None)
        if request_id is not None:
            payload["request_id"] = request_id
        return json.dumps(payload)


def setup_logging(service_name: str, level: int = logging.INFO) -> None:
    """Configures the root logger once per process. Call at service startup
    (main.py / streamlit_app.py module level), before any `logger.info(...)`
    call that should actually be captured — every existing
    `logging.getLogger(__name__)` call already in each service starts
    emitting structured JSON once this has run, no other code changes
    needed.
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter(service_name))
    root.addHandler(handler)
