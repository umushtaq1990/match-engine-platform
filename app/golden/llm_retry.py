"""A real timeout hit on the very first candidate of a full 500-candidate
batch run (confirmed live, not theoretical): app/extraction/ollama_client.py's
fixed 60s timeout is tight enough that Ollama being transiently busy/cold
can trip it, which would otherwise crash the whole multi-hour batch and
lose all progress on whichever candidate was mid-flight. Wraps `generate()`
with a short retry instead of threading a longer timeout through the
shared client (which every other caller also uses and whose default is
fine for their shorter prompts).
"""
import logging
import time

import httpx

from app.extraction.ollama_client import generate

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5.0


def generate_with_retry(model: str, prompt: str, attempts: int = MAX_ATTEMPTS) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return generate(model, prompt)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_exc = exc
            logger.warning("Ollama call failed (attempt %s/%s): %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(RETRY_DELAY_SECONDS)
    raise last_exc
