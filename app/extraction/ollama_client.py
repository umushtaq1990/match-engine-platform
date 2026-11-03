"""Thin client for the shared Ollama service (Phase 15).

Unlike app/api/analytics_client.py, failures here are NOT swallowed — the
caller (Use Case C extraction) has nothing useful to return without a real
result, a deliberate difference documented in docs/model-serving.md's
consumer-pattern section and docs/skill-extraction.md's service boundary.
"""
import os

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
_TIMEOUT_SECONDS = 60.0


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
    return response.json()["response"]


def embed(model: str, text: str) -> list[float]:
    response = httpx.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["embedding"]
