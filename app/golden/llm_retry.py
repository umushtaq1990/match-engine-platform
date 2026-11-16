"""A real timeout hit on the very first candidate of a full 500-candidate
batch run (confirmed live, not theoretical): app/extraction/ollama_client.py's
fixed 60s timeout is tight enough that Ollama being transiently busy/cold
can trip it, which would otherwise crash the whole multi-hour batch and
lose all progress on whichever candidate was mid-flight. Wraps `generate()`
with a short retry instead of threading a longer timeout through the
shared client (which every other caller also uses and whose default is
fine for their shorter prompts).

golden-dataset v2: generalized to accept either backend's generate()
function (app.extraction.ollama_client or app.golden.vllm_client -- both
share the same (model, prompt, response_format=None, temperature=None)
signature), via `make_generate_with_retry`, so generate_cvs.py/
generate_jobs.py can use --backend vllm for GPU-speed regeneration the
same way app/golden/run_extraction_eval.py already does. That script had
its own private copy of this exact factory (`_make_generate_with_retry`)
before this generalization -- moved here as the one shared implementation,
imported back into run_extraction_eval.py instead of duplicated.
`generate_with_retry` below is kept as the plain, Ollama-bound default for
any caller that doesn't need to pick a backend.
"""
import logging
import time

import httpx

from app.extraction.ollama_client import generate as _ollama_generate

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5.0


def make_generate_with_retry(backend_generate, attempts: int = MAX_ATTEMPTS):
    def _generate_with_retry(model, prompt, response_format=None, temperature=None):
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return backend_generate(
                    model, prompt, response_format=response_format, temperature=temperature,
                )
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                last_exc = exc
                logger.warning(
                    "%s call failed (attempt %s/%s): %s",
                    backend_generate.__module__, attempt, attempts, exc,
                )
                if attempt < attempts:
                    time.sleep(RETRY_DELAY_SECONDS)
        raise last_exc
    return _generate_with_retry


generate_with_retry = make_generate_with_retry(_ollama_generate)
