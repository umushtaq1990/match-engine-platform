"""Hybrid skill-extraction pipeline (Use Case C, Phase 16): an LLM extracts
candidate skill phrases + a required/preferred guess, then embedding
similarity canonicalizes each phrase onto the existing skill taxonomy with a
measurable confidence score. See docs/skill-extraction.md for the full
rationale.
"""
import json
import math

from sqlalchemy.orm import Session

from app.extraction.ollama_client import embed, generate
from app.extraction.skill_cache import get_reference_embeddings

DEFAULT_LLM_MODEL = "llama3.2:1b"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_SIMILARITY_THRESHOLD = 0.55
PROMPT_VERSION = "v2"  # v1 used the loose "format": "json" string — see schema fix below

_VALID_LEVELS = {"required", "preferred"}

# A JSON Schema, not the loose "format": "json" string — found by real testing
# (docs/skill-extraction.md) that a 1B model given only the loose string
# ignored the "respond with an array" instruction and emitted a single
# malformed object. The schema form makes Ollama constrain the output
# structurally, not just require valid JSON of any shape.
_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "phrase": {"type": "string"},
            "requirement_level": {"type": "string", "enum": ["required", "preferred"]},
        },
        "required": ["phrase", "requirement_level"],
    },
}

_EXTRACTION_PROMPT = """You extract skills mentioned in job postings or resumes, including both \
technical skills (e.g. programming languages, tools, platforms) and non-technical skills (e.g. \
communication, sales, trades, healthcare, creative, office skills).

For each skill mentioned in the text below, output one entry with the exact phrase used and \
whether it is required (mandatory/must-have) or preferred (nice-to-have/bonus/plus). If no \
skills are mentioned, output an empty list.

Text:
\"\"\"
{text}
\"\"\"
"""


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _parse_llm_output(raw: str) -> list[dict]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []

    results = []
    for item in parsed:
        if not isinstance(item, dict) or "phrase" not in item:
            continue
        level = item.get("requirement_level")
        results.append({
            "phrase": str(item["phrase"]),
            "requirement_level": level if level in _VALID_LEVELS else "required",
        })
    return results


def extract_skills(
    text: str,
    session: Session,
    llm_model: str = DEFAULT_LLM_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> dict:
    raw = generate(llm_model, _EXTRACTION_PROMPT.format(text=text), response_format=_RESPONSE_SCHEMA)
    extracted = _parse_llm_output(raw)
    reference = get_reference_embeddings(session, embedding_model)

    matches = []
    unmatched = []
    for item in extracted:
        phrase_embedding = embed(embedding_model, item["phrase"])
        best_skill_id, best_score = None, -1.0
        for skill_id, _ref_text, ref_embedding in reference:
            score = _cosine_similarity(phrase_embedding, ref_embedding)
            if score > best_score:
                best_skill_id, best_score = skill_id, score

        if best_skill_id is not None and best_score >= similarity_threshold:
            matches.append({
                "skill_id": best_skill_id,
                "requirement_level": item["requirement_level"],
                "confidence": round(best_score, 4),
                "source_phrase": item["phrase"],
            })
        else:
            unmatched.append({
                "phrase": item["phrase"],
                "best_score": round(best_score, 4) if best_score > -1 else None,
            })

    return {"matches": matches, "unmatched": unmatched}
