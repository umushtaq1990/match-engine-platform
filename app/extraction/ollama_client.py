"""Thin client for the shared Ollama service (Phase 15).

Unlike app/api/analytics_client.py, failures here are NOT swallowed — the
caller (Use Case C extraction) has nothing useful to return without a real
result, a deliberate difference documented in docs/model-serving.md's
consumer-pattern section and docs/skill-extraction.md's service boundary.
"""
import httpx
import mlflow
from mlflow.entities import SpanType

from app.common.config import settings

OLLAMA_URL = settings.ollama_url
# 60s was tuned around qwen2.5:1.5b's typical decode speed -- confirmed
# live it's genuinely too short for bigger CPU-served models (qwen2.5:7b,
# gemma2:9b): real single calls exceeded 60s, triggering the caller's
# retry wrapper and inflating apparent latency rather than reflecting
# actual generation speed. Configurable so a bigger-model comparison can
# raise it without touching this shared client's code.
_TIMEOUT_SECONDS = settings.ollama_timeout_seconds


def _set_span_attributes(**attrs) -> None:
    """Best-effort: attaches extra attributes (token counts, durations --
    anything not already part of the traced function's own args/return
    value) to whichever span @mlflow.trace is currently building for this
    call. A no-op, not an error, if tracing isn't configured/reachable --
    MLFLOW_TRACKING_URI being unset or the server being down must never
    break a real extraction call for the sake of observability."""
    try:
        span = mlflow.get_current_active_span()
        if span is not None:
            span.set_attributes(attrs)
    except Exception:
        pass


@mlflow.trace(span_type=SpanType.LLM)
def generate(
    model: str,
    prompt: str,
    response_format: dict | str | None = None,
    temperature: float | None = None,
) -> str:
    """Calls /api/generate. `response_format` can be the loose string "json"
    (valid JSON, any shape) or a JSON Schema dict (Ollama constrains output
    to match it structurally) — see pipeline.py for why this project needs
    the schema form, not the loose one.

    `temperature` defaults to `None` (Ollama's own default, ~0.8), not 0 —
    tried pinning this model to greedy decoding (temperature=0) to remove a
    real measured flakiness (the exact same unambiguous input, "...must have
    strong experience with Python.", sometimes returned an empty extraction
    under sampling), but greedy decoding turned out *worse*, not better:
    tested live against 5 similarly-phrased skill-bearing sentences, it
    returned the correct extraction for only 1 of them and an empty `[ ]` for
    the other 4 — qwen2.5:1.5b's single most-likely completion for this
    prompt/schema combination is often the degenerate empty answer. Sampling
    actually escapes that local optimum some of the time, which is why
    pipeline.py retries instead of pinning temperature — see its docstring."""
    payload = {"model": model, "prompt": prompt, "stream": False}
    if response_format is not None:
        payload["format"] = response_format
    if temperature is not None:
        payload["options"] = {"temperature": temperature}
    response = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json=payload,
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    _set_span_attributes(
        prompt_tokens=data.get("prompt_eval_count"),
        completion_tokens=data.get("eval_count"),
        load_duration_seconds=(data.get("load_duration") or 0) / 1e9,
        total_duration_seconds=(data.get("total_duration") or 0) / 1e9,
    )
    return data["response"]


@mlflow.trace(span_type=SpanType.EMBEDDING)
def embed(model: str, text: str) -> list[float]:
    response = httpx.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["embedding"]


@mlflow.trace(span_type=SpanType.EMBEDDING)
def embed_batch(model: str, texts: list[str]) -> list[list[float]]:
    """Ollama's newer `/api/embed` (plural) takes a list of inputs and
    embeds all of them in one round trip -- confirmed live (0.40.13):
    posting 3 phrases returns 3 embeddings in a single response. Used
    where many short phrases need embedding at once (per-extraction
    phrase matching, the reference skill-taxonomy cache warmup) instead
    of one HTTP call per phrase."""
    if not texts:
        return []
    response = httpx.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": model, "input": texts},
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["embeddings"]
