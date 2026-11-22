"""Picks which LLM client `generate()` the chatbot package calls --
`app.extraction.ollama_client` (CPU, serves whichever Ollama tag is passed)
or `app.golden.vllm_client` (GPU, continuous batching, serves one fixed
model regardless of the `model` arg passed in -- see that module's own
docstring). Both expose the same `generate(model, prompt, response_format,
temperature)` signature, so this is a pure selection, not an adapter.

Mirrors `app.golden.generate_cvs.py`'s existing backend-selection pattern
(`backend_generate = vllm_client.generate if backend == "vllm" else
_ollama_generate`) rather than inventing a new one.
"""
from typing import Callable

from app.extraction.ollama_client import generate as _ollama_generate

GenerateFn = Callable[..., str]

BACKEND_OLLAMA = "ollama"
BACKEND_VLLM = "vllm"


def resolve_generate_fn(backend: str) -> GenerateFn:
    if backend == BACKEND_VLLM:
        from app.golden import vllm_client  # deferred: only needs httpx/mlflow, but keep optional path lazy
        return vllm_client.generate
    if backend == BACKEND_OLLAMA:
        return _ollama_generate
    raise ValueError(f"Unknown LLM backend {backend!r}, expected {BACKEND_OLLAMA!r} or {BACKEND_VLLM!r}")
