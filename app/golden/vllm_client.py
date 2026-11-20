"""Thin client for vLLM's OpenAI-compatible chat completion endpoint -- an
alternative backend to app/extraction/ollama_client.py's generate(), tested
live for the golden-dataset extraction eval to demonstrate real concurrent
throughput (confirmed: ~15 req/s sustained vs Ollama's ~0.3-0.6 req/s on
the same GPU, via continuous batching).

Uses /v1/chat/completions, NOT /v1/completions -- confirmed live this
matters a lot: Llama-3.2-3B-Instruct given a raw prompt via /v1/completions
returned a bare "[ ]" for text that clearly mentioned 4 real skills (near-
zero precision/recall across the full 500-candidate eval, run_id 31) --
it's trained to expect the chat template, not raw completion-style
prompting, unlike Qwen2.5 which tolerates both reasonably. Re-tested with
the exact same prompt via /v1/chat/completions: correctly extracted all 4
skills. Chat completions is the only format that's fair across model
families, so this client uses it exclusively, even though Qwen worked
passably with raw completions too.

response_format must be `{"type": "json_schema", "json_schema": {"name": ..., "schema": ...}}`
on vLLM, NOT the older `guided_json` field (confirmed live -- guided_json
is silently ignored by this vLLM version, 0.33.0, producing free-form text
instead of schema-conformant JSON).

Only the LLM-generation call is routed here -- embeddings still go through
Ollama's nomic-embed-text (vLLM here only serves the one completion model),
so Ollama must stay running alongside vLLM for a full extraction run.
"""
import httpx
import mlflow
from mlflow.entities import SpanType

from app.common.config import settings

VLLM_URL = settings.vllm_url
VLLM_MODEL = settings.vllm_model
_TIMEOUT_SECONDS = 120.0


def _set_span_attributes(**attrs) -> None:
    """Best-effort, same reasoning as ollama_client.py's copy -- a tracing
    no-op must never break a real call."""
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
    max_tokens: int = 250,
) -> str:
    """Same signature as app.extraction.ollama_client.generate -- `model` is
    ignored (vLLM serves exactly one model, VLLM_MODEL, per server process)
    so this can be monkeypatched in as a drop-in replacement.

    `max_tokens` defaults to 250 -- the extraction eval's original budget
    (vLLM requires prompt_tokens + max_tokens <= max_model_len just to admit
    a request regardless of actual output length; real extraction outputs
    are short skill lists, observed well under 150 completion tokens, so
    250 leaves headroom without needing a bigger max_model_len, which costs
    real GPU memory we don't always have to spare). A caller generating
    free-text prose instead of a short structured list (e.g. the chatbot's
    answer-generation step) should pass a larger value explicitly -- 250
    was flagged as a plausible truncation cause for chatbot answers that
    looked cut short."""
    payload = {
        "model": VLLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if isinstance(response_format, dict):
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "extraction", "schema": response_format},
        }
    if temperature is not None:
        payload["temperature"] = temperature
    response = httpx.post(f"{VLLM_URL}/v1/chat/completions", json=payload, timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    usage = data.get("usage") or {}
    _set_span_attributes(
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )
    return data["choices"][0]["message"]["content"]
