"""Chatbot RAG evaluation harness -- same two-tier shape as
`app.embeddings.run_embedding_eval`/`app.evaluation.run_evaluation`: MLflow
for run-level aggregate params/metrics (best-effort, never breaks a real
run if the tracking server is down), Postgres (`ChatEvalResult`) for
per-question drill-down detail.

Run with: python -m app.chatbot.eval.run_chatbot_eval [--top-k N]
  [--n-candidates N] [--n-jobs N] [--n-skills N] [--n-courses N]

`retrieval_mode="agent"` for every row this produces -- the full LangGraph
agent decides per question whether to call `search_knowledge_base` (vector)
or `lookup_structured_data` (exact DB lookup by id), same as the live chat
page. This replaced an earlier `retrieval_mode="vector"` harness that called
`retrieve_chunks` directly and never exercised the lookup tool -- confirmed
live (manual diagnostic against the real pgvector index) that candidate/job
ID questions essentially never retrieve their correct chunk by embedding
similarity alone: `all-minilm` clusters by semantic shape ("years of
experience", generic job-posting boilerplate), and a literal id token like
"#2522" carries ~zero embedding weight among ~2,000-5,000 near-identical-
shaped chunks. That's exactly what `lookup_structured_data` exists to
answer instead. Old `retrieval_mode="vector"` rows are left in Postgres for
before/after comparison, not deleted.

Phase 2 ("graph") and Phase 3 ("hybrid") add further `retrieval_mode`
values under the same schema/columns, not a new harness.

Breaks metrics down by `expected_source_type` (candidate/job/skill/course)
in addition to the overall average -- a flat average hides that, e.g.,
skill/course questions might score very differently from candidate/job
ones; both the aggregate and the per-category numbers are logged to
MLflow and printed.
"""
import argparse
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import mlflow

from app.chatbot import chat_history
from app.chatbot.eval.golden_questions import build_golden_questions
from app.chatbot.eval.metrics import score_coherence, score_faithfulness, score_groundedness, score_relevance
from app.chatbot.graph import run_chat_turn
from app.chatbot.llm_backend import GenerateFn, resolve_generate_fn
from app.chatbot.models import ChatEvalResult
from app.chatbot.tools import TOOL_LOOKUP_STRUCTURED_DATA
from app.common.config import settings
from app.db import SessionLocal
from app.evaluation.metrics import precision_at_k, recall_at_k

logger = logging.getLogger(__name__)

MLFLOW_EXPERIMENT_NAME = "jme-chatbot-rag"
MLFLOW_TRACKING_URI = settings.mlflow_tracking_uri
RETRIEVAL_MODE = "agent"
EVAL_USER_ID = "eval-harness"
METRIC_NAMES = ("relevance", "coherence", "faithfulness", "groundedness", "precision_at_k", "recall_at_k")

# Maps `expected_source_type` (golden_questions.py's naming, shared with
# DocumentChunk.source_type) to the id field `lookup_structured_data`
# returns for that entity type (tools.py's naming) -- used to check a
# lookup call actually resolved to the *expected* row, regardless of
# whether the agent looked it up by id (candidate/job) or by name
# (skill/course, which have no stable id a question would ever name).
_SOURCE_TYPE_TO_RESULT_ID_FIELD = {
    "candidate_profile": "candidate_id", "job_description": "job_id",
    "skill": "skill_id", "course": "course_id",
}


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _log_to_mlflow(
    run_at: datetime, config_label: str, n: int, averages: dict[str, float], by_category: dict[str, dict],
) -> None:
    os.environ.setdefault("MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT", "true")
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
        with mlflow.start_run(run_name=f"chatbot-eval-{run_at.isoformat()}"):
            mlflow.log_params({
                "retrieval_mode": RETRIEVAL_MODE, "config_label": config_label, "n_questions": n,
            })
            mlflow.log_metrics(averages)
            for category, cat_averages in by_category.items():
                mlflow.log_metrics({f"{name}_{category}": value for name, value in cat_averages.items()})
    except Exception:
        logger.warning(
            "MLflow logging failed (tracking server unreachable?) -- run still persisted to Postgres.",
            exc_info=True,
        )


def _eval_one_question(item: dict, top_k: int, generate_fn: GenerateFn) -> dict:
    """Runs one question end-to-end and scores it -- the unit of work
    parallelized across a thread pool. Opens and closes its own DB session
    (a SQLAlchemy `Session` isn't thread-safe to share across workers,
    unlike `app.extraction.run_extraction_eval`'s `max_workers` pattern,
    whose per-example work makes no DB writes of its own); the caller
    persists `ChatEvalResult` rows afterward on its own session, serially.
    """
    worker_session = SessionLocal()
    try:
        # Eval uses the admin role deliberately -- it's measuring the full
        # knowledge base's retrieval/answer quality, not access control
        # (that's a separate negative test, see the plan's Verification
        # section, not this averaged-metrics harness). Each question gets
        # its own isolated ChatSession rather than one shared session,
        # since these are independent eval questions, not a real
        # conversation -- sharing one session would feed each question
        # unrelated prior Q&As as "history" and grow every prompt linearly.
        chat_session = chat_history.create_session(worker_session, user_id=EVAL_USER_ID, role="admin")
        turn = run_chat_turn(
            worker_session, chat_session.id, role="admin", question=item["question"], generate_fn=generate_fn,
        )
        answer = turn["answer"]
        chunks = turn["retrieved_chunks"]
        retrieved_ids = [c["chunk_id"] for c in chunks]
        context = "\n\n".join(c["text"] for c in chunks) or "(no information found)"

        # lookup_structured_data resolves an entity by id (candidate/job)
        # or by exact name (skill/course -- there's no stable id a
        # question would ever name for those) -- no DocumentChunk involved
        # at all either way, so chunk-based precision/recall doesn't
        # apply. Checking the *resolved result's* id field (not the raw
        # tool-call args) is what makes this correct regardless of which
        # lookup path the agent took. If it resolved to the *expected*
        # row, that's a perfect retrieval (the right source, by
        # construction, not an embedding guess); score it 1.0/1.0 rather
        # than 0.0/0.0 as "no chunks retrieved" would otherwise read.
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

        relevance_score, _ = score_relevance(item["question"], answer, generate_fn)
        coherence_score, _ = score_coherence(answer, generate_fn)
        faithfulness_score, _ = score_faithfulness(context, answer, generate_fn)
        groundedness_score, _ = score_groundedness(context, answer, generate_fn)
    finally:
        worker_session.close()

    return {
        "question": item["question"], "expected_answer": item["expected_answer"],
        "category": item["expected_source_type"], "answer": answer, "retrieved_ids": retrieved_ids,
        "relevance": relevance_score, "coherence": coherence_score,
        "faithfulness": faithfulness_score, "groundedness": groundedness_score,
        "precision": precision, "recall": recall,
    }


def run_eval(
    session, top_k: int = 5, n_candidates: int = 10, n_jobs: int = 10,
    n_skills: int = 10, n_courses: int = 10, embedding_model: str = "all-minilm",
    backend: str = "ollama", max_workers: int = 1,
) -> tuple[list[ChatEvalResult], dict[str, float], dict[str, dict[str, float]]]:
    questions = build_golden_questions(
        session, n_candidates=n_candidates, n_jobs=n_jobs, n_skills=n_skills, n_courses=n_courses,
    )
    config_label = f"retrieval_mode={RETRIEVAL_MODE},backend={backend},embedding_model={embedding_model},top_k={top_k}"
    run_at = datetime.now(timezone.utc)
    generate_fn = resolve_generate_fn(backend)

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            per_question = list(pool.map(
                lambda item: _eval_one_question(item, top_k, generate_fn), questions,
            ))
    else:
        per_question = [_eval_one_question(item, top_k, generate_fn) for item in questions]

    results = []
    metric_totals: dict[str, list[float]] = {name: [] for name in METRIC_NAMES}
    by_category_totals: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {name: [] for name in METRIC_NAMES}
    )

    for row in per_question:
        result = ChatEvalResult(
            run_at=run_at, question=row["question"], expected_answer=row["expected_answer"],
            retrieval_mode=RETRIEVAL_MODE, retrieved_chunk_ids=row["retrieved_ids"], generated_answer=row["answer"],
            relevance_score=row["relevance"], coherence_score=row["coherence"],
            faithfulness_score=row["faithfulness"], groundedness_score=row["groundedness"],
            precision_at_k=round(row["precision"], 4), recall_at_k=round(row["recall"], 4),
            config_label=config_label,
        )
        session.add(result)
        results.append(result)

        values = {
            "relevance": row["relevance"], "coherence": row["coherence"],
            "faithfulness": row["faithfulness"], "groundedness": row["groundedness"],
            "precision_at_k": row["precision"], "recall_at_k": row["recall"],
        }
        for name, value in values.items():
            metric_totals[name].append(value)
            by_category_totals[row["category"]][name].append(value)

    session.commit()
    averages = {name: _avg(values) for name, values in metric_totals.items()}
    by_category = {
        category: {name: _avg(values) for name, values in totals.items()}
        for category, totals in by_category_totals.items()
    }
    _log_to_mlflow(run_at, config_label, len(questions), averages, by_category)
    return results, averages, by_category


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--n-candidates", type=int, default=10)
    parser.add_argument("--n-jobs", type=int, default=10)
    parser.add_argument("--n-skills", type=int, default=10)
    parser.add_argument("--n-courses", type=int, default=10)
    parser.add_argument(
        "--backend", choices=["ollama", "vllm"], default="ollama",
        help="ollama (CPU, default, matches the live chat page) or vllm (GPU, continuous batching -- "
             "needs `docker compose --profile tools up -d vllm` running first).",
    )
    parser.add_argument(
        "--max-workers", type=int, default=1,
        help="questions evaluated concurrently via a thread pool. >1 only makes sense with --backend vllm "
             "(Ollama serves one request at a time anyway, same as app.extraction.run_extraction_eval).",
    )
    args = parser.parse_args()

    db_session = SessionLocal()
    _, avgs, by_cat = run_eval(
        db_session, top_k=args.top_k, n_candidates=args.n_candidates, n_jobs=args.n_jobs,
        n_skills=args.n_skills, n_courses=args.n_courses, backend=args.backend, max_workers=args.max_workers,
    )
    print(f"Chatbot eval ({RETRIEVAL_MODE}, backend={args.backend}), overall: {avgs}")
    for category, cat_avgs in by_cat.items():
        print(f"  [{category}] {cat_avgs}")
    db_session.close()
