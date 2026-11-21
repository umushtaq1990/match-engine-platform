"""A second, independent evaluation harness -- RAGAS -- run alongside this
project's own hand-rolled `eval/metrics.py`, as a cross-check: do our custom
LLM-judge prompts (relevance/coherence/faithfulness/groundedness) agree
with a published, community-vetted RAG metric implementation, or were we
measuring something our own prompts happen to reward?

Reuses this project's own data/orchestration exactly as `run_chatbot_eval.py`
does -- `build_golden_questions()` for the ground-truth set,
`graph.run_chat_turn()` for the real agent (so RAGAS judges the same
tool-calling path the live chat page runs, not a simplified retrieve-then-
generate stand-in). Only the *scoring* is RAGAS's: `faithfulness` (does the
answer avoid claims the context doesn't support), `answer_relevancy` (does
the answer actually address the question), `context_precision`/
`context_recall` (did the retrieved context contain what was needed, and
was it ranked usefully) -- RAGAS's standard 4, the closest match to this
project's own 4 metrics without inventing a 1:1 mapping that doesn't exist
(RAGAS has no direct "coherence" equivalent; "groundedness" and
"faithfulness" are effectively the same question asked two ways).

RAGAS needs its own LLM (structured-output judge calls) and embeddings
(answer_relevancy embeds the question and a few regenerated "ideal
questions" from the answer, then compares via cosine similarity) --
both point at this project's existing Ollama service, not a hosted API:
  - LLM: `ragas.llms.llm_factory` wraps an `openai.OpenAI` client pointed at
    Ollama's own OpenAI-compatible endpoint (`/v1`) -- Ollama serves this
    alongside its native `/api/generate` (which `ollama_client.py` uses
    instead, for everything else in this project).
  - Embeddings: `answer_relevancy` is one of RAGAS's *legacy* metric
    classes (`ragas.metrics.answer_relevancy`, not the newer
    `ragas.metrics.collections` ones this package's installed version
    (0.4.4) is migrating to) -- it expects the older embeddings interface
    (`embed_query`/`embed_documents`), which RAGAS's own current
    `embedding_factory` no longer implements (confirmed live: raises
    `AttributeError: 'OpenAIEmbeddings' object has no attribute
    'embed_query'`). `langchain_ollama.OllamaEmbeddings` wrapped in
    `ragas.embeddings.LangchainEmbeddingsWrapper` still implements that
    older interface and was confirmed live to work.

Run with: python -m app.chatbot.eval.run_ragas_eval [--n-candidates N]
  [--n-jobs N] [--n-skills N] [--n-courses N] [--judge-model NAME]
"""
import argparse
import json
import logging

from langchain_ollama import OllamaEmbeddings
from openai import OpenAI
from ragas import EvaluationDataset, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import llm_factory
from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

from app.chatbot import chat_history
from app.chatbot.eval.golden_questions import build_golden_questions
from app.chatbot.graph import run_chat_turn
from app.chatbot.llm_backend import resolve_generate_fn
from app.common.config import settings
from app.db import SessionLocal

logger = logging.getLogger(__name__)

EVAL_USER_ID = "ragas-eval-harness"
_EMBEDDING_MODEL = "nomic-embed-text"


def _build_context(tool_results: list[dict], retrieved_chunks: list[dict]) -> list[str]:
    """Mirrors `run_chatbot_eval.py`'s own judge-context construction
    exactly (see that module's docstring for the live bug this fixes) --
    built from `tool_results` (the lookup JSON) when any tool fired, chunk
    text otherwise. RAGAS's `retrieved_contexts` wants a list of strings,
    not one combined string -- one string per tool result / chunk, so
    `context_precision`/`context_recall` can judge each independently."""
    if tool_results:
        return [json.dumps(r, default=str) for r in tool_results]
    if retrieved_chunks:
        return [c["text"] for c in retrieved_chunks]
    return ["(no information found)"]


def _collect_samples(
    session, questions: list[dict], generate_fn, model: str, enable_graph_tool: bool = False,
) -> list[dict]:
    rows = []
    for item in questions:
        chat_session = chat_history.create_session(session, user_id=EVAL_USER_ID, role="admin")
        try:
            turn = run_chat_turn(
                session, chat_session.id, role="admin", question=item["question"],
                generate_fn=generate_fn, model=model, enable_graph_tool=enable_graph_tool,
            )
        except Exception:
            logger.warning("Question failed, skipping: %r", item["question"], exc_info=True)
            continue
        rows.append({
            "user_input": item["question"],
            "response": turn["answer"],
            "retrieved_contexts": _build_context(turn["tool_results"], turn["retrieved_chunks"]),
            "reference": item["expected_answer"],
        })
    return rows


def run_ragas_eval(
    session, n_candidates: int = 10, n_jobs: int = 10, n_skills: int = 10, n_courses: int = 10,
    model: str = "qwen2.5:1.5b", judge_model: str = "qwen2.5:1.5b", enable_graph_tool: bool = False,
    n_candidate_graph: int = 0, n_job_graph: int = 0,
) -> dict[str, float]:
    questions = build_golden_questions(
        session, n_candidates=n_candidates, n_jobs=n_jobs, n_skills=n_skills, n_courses=n_courses,
        n_candidate_graph=n_candidate_graph, n_job_graph=n_job_graph,
    )
    generate_fn = resolve_generate_fn("ollama")
    rows = _collect_samples(session, questions, generate_fn, model, enable_graph_tool=enable_graph_tool)
    logger.info("Collected %d/%d samples (failures skipped).", len(rows), len(questions))

    client = OpenAI(base_url=f"{settings.ollama_url}/v1", api_key="ollama")
    judge_llm = llm_factory(judge_model, provider="openai", client=client)
    judge_embeddings = LangchainEmbeddingsWrapper(
        OllamaEmbeddings(model=_EMBEDDING_MODEL, base_url=settings.ollama_url)
    )

    dataset = EvaluationDataset.from_list(rows)
    result = evaluate(
        dataset, metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=judge_llm, embeddings=judge_embeddings,
    )
    # `EvaluationResult` has no dict()/mapping conversion of its own --
    # `.scores` is a list of one {metric_name: score} dict per row
    # (confirmed live: `dict(result)` raises `KeyError: 0`, since
    # `EvaluationResult.__getitem__` indexes by *metric name*, not
    # position). A score can be `nan` for a row RAGAS's own metric
    # couldn't compute (e.g. an empty/degenerate answer) -- averaged with
    # `nan`-skipping, not treated as a hard 0.
    metric_names = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    averages = {}
    for name in metric_names:
        values = [row[name] for row in result.scores if name in row and row[name] == row[name]]  # NaN != NaN
        averages[name] = round(sum(values) / len(values), 4) if values else float("nan")
    return averages


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-candidates", type=int, default=10)
    parser.add_argument("--n-jobs", type=int, default=10)
    parser.add_argument("--n-skills", type=int, default=10)
    parser.add_argument("--n-courses", type=int, default=10)
    parser.add_argument(
        "--model", default="qwen2.5:1.5b",
        help="Ollama model for answer generation (the agent under test).",
    )
    parser.add_argument(
        "--judge-model", default="qwen2.5:1.5b",
        help="Ollama model for RAGAS's own judge calls, independent of --model.",
    )
    parser.add_argument(
        "--enable-graph-tool", action="store_true",
        help="offer lookup_graph_context (Phase 2's knowledge-graph relationship tool), to cross-check "
             "run_chatbot_eval.py's candidate_graph/job_graph category with RAGAS's own metrics.",
    )
    parser.add_argument("--n-candidate-graph", type=int, default=0)
    parser.add_argument("--n-job-graph", type=int, default=0)
    args = parser.parse_args()

    db_session = SessionLocal()
    scores = run_ragas_eval(
        db_session, n_candidates=args.n_candidates, n_jobs=args.n_jobs,
        n_skills=args.n_skills, n_courses=args.n_courses, model=args.model, judge_model=args.judge_model,
        enable_graph_tool=args.enable_graph_tool, n_candidate_graph=args.n_candidate_graph,
        n_job_graph=args.n_job_graph,
    )
    print(f"RAGAS eval: {scores}")
    db_session.close()
