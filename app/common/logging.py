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

# Attributes stdlib `logging.LogRecord` always sets itself -- anything else
# found on a record came from a caller's `extra={...}` and should be
# surfaced, so one caller's `request_id` and another's `candidate_id`/
# `example_id` (see app/golden/*.py) both get the same correlation
# treatment without this formatter hardcoding every field name a future
# caller might want.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord(
    "", 0, "", 0, "", (), None,
).__dict__) | {"message", "asctime"}


class _JSONFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc)
        payload = {
            "timestamp": timestamp.isoformat(),
            "level": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS:
                payload[key] = value
        return json.dumps(payload, default=str)


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
