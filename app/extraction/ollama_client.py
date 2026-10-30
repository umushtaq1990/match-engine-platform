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


def generate(model: str, prompt: str, response_format: dict | str | None = None) -> str:
    """Calls /api/generate. `response_format` can be the loose string "json"
    (valid JSON, any shape) or a JSON Schema dict (Ollama constrains output
    to match it structurally) — see pipeline.py for why this project needs
    the schema form, not the loose one."""
    payload = {"model": model, "prompt": prompt, "stream": False}
    if response_format is not None:
        payload["format"] = response_format
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
