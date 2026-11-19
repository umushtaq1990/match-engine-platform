"""Chatbot RAG evaluation harness -- same two-tier shape as
`app.embeddings.run_embedding_eval`/`app.evaluation.run_evaluation`: MLflow
for run-level aggregate params/metrics (best-effort, never breaks a real
run if the tracking server is down), Postgres (`ChatEvalResult`) for
per-question drill-down detail.

Run with: python -m app.chatbot.eval.run_chatbot_eval [--top-k N]
  [--n-candidates N] [--n-jobs N]

`retrieval_mode="vector"` for every row this produces -- Phase 2 ("graph")
and Phase 3 ("hybrid") add rows under the same schema/columns for direct
comparison, not a new harness.
"""
import argparse
import logging
import os
from datetime import datetime, timezone

import mlflow

from app.chatbot.eval.golden_questions import build_golden_questions
from app.chatbot.eval.metrics import score_coherence, score_faithfulness, score_groundedness, score_relevance
from app.chatbot.models import ChatEvalResult
from app.chatbot.retrieval import retrieve_chunks
from app.common.config import settings
from app.db import SessionLocal
from app.evaluation.metrics import precision_at_k, recall_at_k
from app.extraction.ollama_client import generate

logger = logging.getLogger(__name__)

MLFLOW_EXPERIMENT_NAME = "jme-chatbot-rag"
MLFLOW_TRACKING_URI = settings.mlflow_tracking_uri
RETRIEVAL_MODE = "vector"

_ANSWER_PROMPT = """Answer the question using ONLY the context below. If the context doesn't answer \
the question, say so. Describe things neutrally -- an absent field is unknown, not a deficiency.

Context:
{context}

Question: {question}

Answer in 1-3 plain sentences.
"""


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _log_to_mlflow(run_at: datetime, config_label: str, n: int, averages: dict[str, float]) -> None:
    os.environ.setdefault("MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT", "true")
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
        with mlflow.start_run(run_name=f"chatbot-eval-{run_at.isoformat()}"):
            mlflow.log_params({
                "retrieval_mode": RETRIEVAL_MODE, "config_label": config_label, "n_questions": n,
            })
            mlflow.log_metrics(averages)
    except Exception:
        logger.warning(
            "MLflow logging failed (tracking server unreachable?) -- run still persisted to Postgres.",
            exc_info=True,
        )


def run_eval(
    session, top_k: int = 5, n_candidates: int = 10, n_jobs: int = 10, embedding_model: str = "all-minilm",
) -> list[ChatEvalResult]:
    questions = build_golden_questions(session, n_candidates=n_candidates, n_jobs=n_jobs)
    config_label = f"retrieval_mode={RETRIEVAL_MODE},embedding_model={embedding_model},top_k={top_k}"
    run_at = datetime.now(timezone.utc)

    results = []
    metric_totals: dict[str, list[float]] = {
        "relevance": [], "coherence": [], "faithfulness": [], "groundedness": [],
        "precision_at_k": [], "recall_at_k": [],
    }

    for item in questions:
        # Eval uses the admin role deliberately -- it's measuring the full
        # knowledge base's retrieval/answer quality, not access control
        # (that's a separate negative test, see the plan's Verification
        # section, not this averaged-metrics harness).
        chunks = retrieve_chunks(
            session, item["question"], role="admin", top_k=top_k, embedding_model=embedding_model,
        )
        context = "\n\n".join(c.text for c in chunks) or "(no information found)"
        answer = generate("qwen2.5:1.5b", _ANSWER_PROMPT.format(context=context, question=item["question"]))

        relevant_ids = {
            c.chunk_id for c in chunks
            if c.source_type == item["expected_source_type"]
            and c.source_entity_id == item["expected_source_entity_id"]
        }
        retrieved_ids = [c.chunk_id for c in chunks]
        precision = precision_at_k(retrieved_ids, relevant_ids, top_k) if relevant_ids else 0.0
        recall = recall_at_k(retrieved_ids, relevant_ids, top_k) if relevant_ids else 0.0

        relevance_score, _ = score_relevance(item["question"], answer)
        coherence_score, _ = score_coherence(answer)
        faithfulness_score, _ = score_faithfulness(context, answer)
        groundedness_score, _ = score_groundedness(context, answer)

        result = ChatEvalResult(
            run_at=run_at, question=item["question"], expected_answer=item["expected_answer"],
            retrieval_mode=RETRIEVAL_MODE, retrieved_chunk_ids=retrieved_ids, generated_answer=answer,
            relevance_score=relevance_score, coherence_score=coherence_score,
            faithfulness_score=faithfulness_score, groundedness_score=groundedness_score,
            precision_at_k=round(precision, 4), recall_at_k=round(recall, 4), config_label=config_label,
        )
        session.add(result)
        results.append(result)

        metric_totals["relevance"].append(relevance_score)
        metric_totals["coherence"].append(coherence_score)
        metric_totals["faithfulness"].append(faithfulness_score)
        metric_totals["groundedness"].append(groundedness_score)
        metric_totals["precision_at_k"].append(precision)
        metric_totals["recall_at_k"].append(recall)

    session.commit()
    averages = {name: _avg(values) for name, values in metric_totals.items()}
    _log_to_mlflow(run_at, config_label, len(questions), averages)
    return results, averages


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--n-candidates", type=int, default=10)
    parser.add_argument("--n-jobs", type=int, default=10)
    args = parser.parse_args()

    db_session = SessionLocal()
    _, avgs = run_eval(db_session, top_k=args.top_k, n_candidates=args.n_candidates, n_jobs=args.n_jobs)
    print(f"Chatbot eval ({RETRIEVAL_MODE}): {avgs}")
    db_session.close()
