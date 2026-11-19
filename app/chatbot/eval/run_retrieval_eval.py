"""Fast, LLM-judge-free retrieval eval -- precision@k/recall@k only, via
`app.chatbot.graph.run_retrieval_turn` (decide_tools -> execute_tools, no
answer generation). Split out of `run_chatbot_eval.py` deliberately: those
two metrics depend only on which chunks/lookup the agent picked, not on
any judged answer, so bundling them with the 4 LLM-judge calls (relevance/
coherence/faithfulness/groundedness) makes every retrieval experiment
(different top_k, embedding model, chunk size) pay for judge calls whose
result can't change. This harness needs ~1 LLM call per question (the
tool-decision call) instead of ~5, so it's the one to run repeatedly while
iterating on retrieval knobs; run `run_chatbot_eval.py` for a full scored
run including answer quality once retrieval looks right.

Does not write to `ChatEvalResult` (that table's own docstring scopes it
to "one golden question's scored run" -- the full LLM-judged kind) or to
`ChatSession`/`ChatMessage` (no answer is ever generated, so there's
nothing to persist as chat history). Logs only the aggregate numbers to
MLflow, same best-effort pattern as `run_chatbot_eval.py`.

Run with: python -m app.chatbot.eval.run_retrieval_eval [--top-k N]
  [--n-candidates N] [--n-jobs N] [--n-skills N] [--n-courses N]
  [--backend ollama|vllm] [--max-workers N]
"""
import argparse
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import mlflow

from app.chatbot.eval.golden_questions import build_golden_questions
from app.chatbot.graph import run_retrieval_turn
from app.chatbot.llm_backend import GenerateFn, resolve_generate_fn
from app.chatbot.tools import TOOL_LOOKUP_STRUCTURED_DATA
from app.common.config import settings
from app.db import SessionLocal
from app.evaluation.metrics import precision_at_k, recall_at_k

logger = logging.getLogger(__name__)

MLFLOW_EXPERIMENT_NAME = "jme-chatbot-retrieval"
MLFLOW_TRACKING_URI = settings.mlflow_tracking_uri
RETRIEVAL_MODE = "agent"

# See app.chatbot.eval.run_chatbot_eval's identical mapping for why this
# checks the lookup's *resolved result* id field, not the raw call args.
_SOURCE_TYPE_TO_RESULT_ID_FIELD = {
    "candidate_profile": "candidate_id", "job_description": "job_id",
    "skill": "skill_id", "course": "course_id",
}


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _log_to_mlflow(run_at: datetime, config_label: str, n: int, averages: dict, by_category: dict) -> None:
    os.environ.setdefault("MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT", "true")
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
        with mlflow.start_run(run_name=f"retrieval-eval-{run_at.isoformat()}"):
            mlflow.log_params({"retrieval_mode": RETRIEVAL_MODE, "config_label": config_label, "n_questions": n})
            mlflow.log_metrics(averages)
            for category, cat_averages in by_category.items():
                mlflow.log_metrics({f"{name}_{category}": value for name, value in cat_averages.items()})
    except Exception:
        logger.warning("MLflow logging failed (tracking server unreachable?).", exc_info=True)


def _eval_one_question(item: dict, top_k: int, generate_fn: GenerateFn) -> dict:
    worker_session = SessionLocal()
    try:
        turn = run_retrieval_turn(worker_session, role="admin", question=item["question"], generate_fn=generate_fn)
        chunks = turn["retrieved_chunks"]
        retrieved_ids = [c["chunk_id"] for c in chunks]

        expected_id_field = _SOURCE_TYPE_TO_RESULT_ID_FIELD.get(item["expected_source_type"])
        lookup_hit = any(
            result.get("tool") == TOOL_LOOKUP_STRUCTURED_DATA
            and isinstance(result.get("result"), dict)
            and result["result"].get(expected_id_field) == item["expected_source_entity_id"]
            for result in turn["tool_results"]
        )
        if lookup_hit:
            precision, recall = 1.0, 1.0
        else:
            relevant_ids = {
                c["chunk_id"] for c in chunks
                if c["source_type"] == item["expected_source_type"]
                and c["source_entity_id"] == item["expected_source_entity_id"]
            }
            precision = precision_at_k(retrieved_ids, relevant_ids, top_k) if relevant_ids else 0.0
            recall = recall_at_k(retrieved_ids, relevant_ids, top_k) if relevant_ids else 0.0
    finally:
        worker_session.close()

    return {"category": item["expected_source_type"], "precision": precision, "recall": recall}


def run_retrieval_eval(
    session, top_k: int = 5, n_candidates: int = 10, n_jobs: int = 10,
    n_skills: int = 10, n_courses: int = 10, embedding_model: str = "all-minilm",
    backend: str = "ollama", max_workers: int = 1,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    questions = build_golden_questions(
        session, n_candidates=n_candidates, n_jobs=n_jobs, n_skills=n_skills, n_courses=n_courses,
    )
    config_label = f"retrieval_mode={RETRIEVAL_MODE},backend={backend},embedding_model={embedding_model},top_k={top_k}"
    run_at = datetime.now(timezone.utc)
    generate_fn = resolve_generate_fn(backend)

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            per_question = list(pool.map(lambda item: _eval_one_question(item, top_k, generate_fn), questions))
    else:
        per_question = [_eval_one_question(item, top_k, generate_fn) for item in questions]

    metric_totals: dict[str, list[float]] = {"precision_at_k": [], "recall_at_k": []}
    by_category_totals: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"precision_at_k": [], "recall_at_k": []}
    )
    for row in per_question:
        metric_totals["precision_at_k"].append(row["precision"])
        metric_totals["recall_at_k"].append(row["recall"])
        by_category_totals[row["category"]]["precision_at_k"].append(row["precision"])
        by_category_totals[row["category"]]["recall_at_k"].append(row["recall"])

    averages = {name: _avg(values) for name, values in metric_totals.items()}
    by_category = {
        category: {name: _avg(values) for name, values in totals.items()}
        for category, totals in by_category_totals.items()
    }
    _log_to_mlflow(run_at, config_label, len(questions), averages, by_category)
    return averages, by_category


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--n-candidates", type=int, default=10)
    parser.add_argument("--n-jobs", type=int, default=10)
    parser.add_argument("--n-skills", type=int, default=10)
    parser.add_argument("--n-courses", type=int, default=10)
    parser.add_argument("--backend", choices=["ollama", "vllm"], default="ollama")
    parser.add_argument("--max-workers", type=int, default=1)
    args = parser.parse_args()

    db_session = SessionLocal()
    avgs, by_cat = run_retrieval_eval(
        db_session, top_k=args.top_k, n_candidates=args.n_candidates, n_jobs=args.n_jobs,
        n_skills=args.n_skills, n_courses=args.n_courses, backend=args.backend, max_workers=args.max_workers,
    )
    print(f"Retrieval eval (backend={args.backend}), overall: {avgs}")
    for category, cat_avgs in by_cat.items():
        print(f"  [{category}] {cat_avgs}")
    db_session.close()
