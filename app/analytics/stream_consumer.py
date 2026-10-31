"""Redis Streams consumer (Phase 21) for recommendation_shown impressions
produced by the matching API's instrumentation hook (app/api/stream_client.py).
See docs/redis-infrastructure.md "Use case 3".

Runs as a single background thread in this process (the analytics service
runs one worker — see docker/Dockerfile.analytics — so no cross-process
consumer coordination is needed yet). Ack-after-write only: a message is
XACK'd once its events are committed to Postgres, never before a successful
write. No retry/dead-letter handling for a consumer that crashes mid-batch —
a real production gap, explicitly deferred to v2 (see
docs/redis-infrastructure.md "Known limitations").
"""
import json
import logging
import os
import threading

import redis
from pydantic import ValidationError

from app.analytics.routes import _to_row
from app.analytics.schemas import EventIn
from app.db import SessionLocal

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL")
STREAM_NAME = "analytics:recommendation_events"
CONSUMER_GROUP = "analytics-ingest"
CONSUMER_NAME = f"analytics-{os.getpid()}"

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _ensure_group(client: redis.Redis) -> None:
    try:
        client.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _handle_message(client: redis.Redis, message_id: str, fields: dict) -> None:
    payload = json.loads(fields["payload"])
    db = SessionLocal()
    try:
        rows = []
        for event in payload:
            try:
                rows.append(_to_row(EventIn(**event)))
            except ValidationError:
                logger.warning("Dropping malformed stream event: %r", event, exc_info=True)
        db.add_all(rows)
        db.commit()
    finally:
        db.close()
    client.xack(STREAM_NAME, CONSUMER_GROUP, message_id)


def _consume_loop() -> None:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    _ensure_group(client)
    logger.info("Analytics Redis Streams consumer started (group=%s)", CONSUMER_GROUP)
    while not _stop_event.is_set():
        try:
            response = client.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME, {STREAM_NAME: ">"}, count=10, block=2000,
            )
        except redis.RedisError:
            logger.warning("Redis Streams read failed, retrying", exc_info=True)
            _stop_event.wait(2)
            continue
        if not response:
            continue
        for _stream_name, messages in response:
            for message_id, fields in messages:
                try:
                    _handle_message(client, message_id, fields)
                except Exception:
                    logger.exception(
                        "Failed to process stream message %s, leaving unacked", message_id
                    )


def start() -> None:
    global _thread
    if REDIS_URL is None:
        logger.info("REDIS_URL not set, Redis Streams consumer not started")
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_consume_loop, daemon=True, name="analytics-stream-consumer")
    _thread.start()


def stop() -> None:
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)
