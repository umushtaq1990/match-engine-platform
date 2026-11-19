"""LLM-as-judge quality metrics for a generated chat answer -- relevance,
coherence, faithfulness, groundedness. Each is a separate judge call with
its own narrow prompt (one metric per call, not one call asked to rate
four things at once) so a judge's attention isn't split and each score is
independently reproducible/debuggable. Structured JSON output via the same
`response_format` mechanism `app.extraction.pipeline` already uses for
extraction, not free-text parsing.

Retrieval-level grading (did we retrieve the right chunk) reuses
`app.evaluation.metrics`'s existing `precision_at_k`/`recall_at_k`
directly -- not reimplemented here.
"""
import json

from app.extraction.ollama_client import generate

_JUDGE_LLM_MODEL = "qwen2.5:1.5b"

_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 5},
        "justification": {"type": "string"},
    },
    "required": ["score", "justification"],
}

_RELEVANCE_PROMPT = """Rate how RELEVANT this answer is to the question, 1 (not relevant at all) to \
5 (directly and fully relevant). Relevance only -- not whether the answer is correct or well-written.

Question: {question}
Answer: {answer}
"""

_COHERENCE_PROMPT = """Rate how COHERENT this answer is, 1 (confusing/contradictory/garbled) to 5 \
(clear, well-structured, easy to follow). Coherence only -- not whether it's factually correct.

Answer: {answer}
"""

_FAITHFULNESS_PROMPT = """Rate how FAITHFUL this answer is to the provided context, 1 (contradicts or \
ignores the context) to 5 (every claim is consistent with the context). An answer that correctly \
says "I don't know" when the context doesn't cover the question is faithful (score 5), not \
unfaithful.

Context:
{context}

Answer: {answer}
"""

_GROUNDEDNESS_PROMPT = """Rate how GROUNDED this answer is, 1 (contains claims not traceable to the \
context at all -- fabricated/hallucinated) to 5 (every specific claim, name, or number traces back \
to something actually in the context).

Context:
{context}

Answer: {answer}
"""


def _judge(prompt: str) -> tuple[float, str]:
    raw = generate(_JUDGE_LLM_MODEL, prompt, response_format=_SCORE_SCHEMA)
    try:
        parsed = json.loads(raw)
        return float(parsed["score"]), str(parsed.get("justification", ""))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return 0.0, "(judge output could not be parsed)"


def score_relevance(question: str, answer: str) -> tuple[float, str]:
    """Returns `(score, justification)`, score in `[1, 5]` (or `0.0` if the
    judge's output couldn't be parsed -- a parsing failure, not a real
    score of zero)."""
    return _judge(_RELEVANCE_PROMPT.format(question=question, answer=answer))


def score_coherence(answer: str) -> tuple[float, str]:
    return _judge(_COHERENCE_PROMPT.format(answer=answer))


def score_faithfulness(context: str, answer: str) -> tuple[float, str]:
    return _judge(_FAITHFULNESS_PROMPT.format(context=context, answer=answer))


def score_groundedness(context: str, answer: str) -> tuple[float, str]:
    return _judge(_GROUNDEDNESS_PROMPT.format(context=context, answer=answer))
