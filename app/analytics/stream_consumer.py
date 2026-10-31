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

# Phase 25: closes the "no retry/dead-letter pipeline for a crashed
# consumer" gap documented in docs/redis-infrastructure.md "Known
# limitations". A message pending (claimed but unacked) for longer than
# MIN_IDLE_MS almost certainly means the consumer that claimed it crashed or
# hung mid-processing — Redis has no way to know that on its own, which is
# exactly what XPENDING/XCLAIM exist to let a *different* consumer detect
# and recover from. After MAX_DELIVERIES attempts, a message is assumed to
# be poison (malformed payload, a bug a retry won't fix) and moved to a
# dead-letter stream instead of blocking the main stream forever.
MIN_IDLE_MS = 30_000
MAX_DELIVERIES = 3
DEAD_LETTER_STREAM = f"{STREAM_NAME}:dead-letter"
DEAD_LETTER_MAXLEN = 10_000

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


def _dead_letter(client: redis.Redis, message_id: str, times_delivered: int) -> None:
    entries = client.xrange(STREAM_NAME, min=message_id, max=message_id)
    if entries:
        _, fields = entries[0]
        client.xadd(
            DEAD_LETTER_STREAM,
            {**fields, "original_id": message_id, "times_delivered": str(times_delivered)},
            maxlen=DEAD_LETTER_MAXLEN, approximate=True,
        )
        logger.error(
            "Message %s exceeded %s delivery attempts, moved to %s",
            message_id, MAX_DELIVERIES, DEAD_LETTER_STREAM,
        )
    else:
        logger.warning(
            "Message %s exceeded %s delivery attempts but its entry was already trimmed "
            "from the stream (MAXLEN) — acking without a dead-letter copy", message_id, MAX_DELIVERIES,
        )
    client.xack(STREAM_NAME, CONSUMER_GROUP, message_id)


def _reclaim_stale_messages(client: redis.Redis) -> None:
    """Scans for messages claimed by some consumer (possibly a crashed one)
    for longer than MIN_IDLE_MS, and either retries them (re-claimed by this
    consumer) or dead-letters them, depending on how many times they've
    already been delivered. Safe to call from multiple consumer processes —
    XCLAIM only succeeds for a message still at the idle time it checked.
    """
    pending = client.xpending_range(
        STREAM_NAME, CONSUMER_GROUP, min="-", max="+", count=10, idle=MIN_IDLE_MS,
    )
    for entry in pending:
        message_id, times_delivered = entry["message_id"], entry["times_delivered"]
        if times_delivered >= MAX_DELIVERIES:
            _dead_letter(client, message_id, times_delivered)
            continue
        claimed = client.xclaim(
            STREAM_NAME, CONSUMER_GROUP, CONSUMER_NAME,
            min_idle_time=MIN_IDLE_MS, message_ids=[message_id],
        )
        for claimed_id, fields in claimed:
            try:
                _handle_message(client, claimed_id, fields)
            except Exception:
                logger.exception(
                    "Reclaimed message %s failed again (attempt %s/%s), will retry later",
                    claimed_id, times_delivered + 1, MAX_DELIVERIES,
                )


def _consume_loop() -> None:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    _ensure_group(client)
    logger.info("Analytics Redis Streams consumer started (group=%s)", CONSUMER_GROUP)
    while not _stop_event.is_set():
        try:
            _reclaim_stale_messages(client)
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
                        "Failed to process stream message %s, leaving unacked for later retry",
                        message_id,
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
