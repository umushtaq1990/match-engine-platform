"""Evaluation harness comparing rule-based, embeddings-blended, and
embeddings-only job-matching quality (Phase 17) — the measured
rule-based-vs-semantic comparison assumption A5/A5b has deferred since
Phase 4. Also scores near-miss skill-pair similarity, a check Phase 4's
exact-occupation ground truth structurally cannot express.

Run with: python -m app.embeddings.run_embedding_eval
  [--embedding-model X] [--k N] [--sample-size N] [--stage1-top-n N]

Tracking split identical to Phase 4/16: MLflow for comparable run-level
params/metrics across model configs, Postgres (embedding_eval_results) for
per-mode detail including the full near-miss pair breakdown.
"""
import argparse
import logging
import os
from datetime import datetime, timezone

import mlflow
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.embeddings.golden_dataset import load_near_miss_pairs
from app.embeddings.models import EmbeddingEvalResult, EntityEmbedding
from app.embeddings.retrieval import rank_jobs_for_candidate_semantic
from app.evaluation.ground_truth import build_ground_truth
from app.evaluation.metrics import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from app.extraction.ollama_client import embed
from app.matching.rank import rank_jobs_for_candidate

logger = logging.getLogger(__name__)

MLFLOW_EXPERIMENT_NAME = "jme-embedding-matching"
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
DEFAULT_EMBEDDING_MODEL = "all-minilm"
DEFAULT_K = 10
DEFAULT_SAMPLE_SIZE = 30
# Calibrated against real measurement (docs/embedding-matching.md): all-minilm
# cosine similarity for the hand-labeled "similar" pairs ranged 0.315-0.649,
# "dissimilar" pairs ranged 0.145-0.252 — 0.28 sits cleanly in that gap.
NEAR_MISS_SIMILARITY_THRESHOLD = 0.28


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def score_near_miss_pairs(embedding_model: str) -> tuple[float, list[dict]]:
    pairs = load_near_miss_pairs()
    if not pairs:
        return 0.0, []

    detail = []
    correct = 0
    for pair in pairs:
        skill_a, skill_b = pair["pair"]
        similarity = _cosine_similarity(embed(embedding_model, skill_a), embed(embedding_model, skill_b))
        predicted = "similar" if similarity >= NEAR_MISS_SIMILARITY_THRESHOLD else "dissimilar"
        is_correct = predicted == pair["expected"]
        correct += int(is_correct)
        detail.append({
            "pair": [skill_a, skill_b], "expected": pair["expected"],
            "similarity": round(similarity, 4), "predicted": predicted, "correct": is_correct,
        })
    return correct / len(pairs), detail


def _candidates_with_embeddings(
    session: Session, candidate_ids: list[int], embedding_model: str
) -> set[int]:
    rows = session.execute(
        select(EntityEmbedding.entity_id).where(
            EntityEmbedding.entity_type == "candidate",
            EntityEmbedding.dimension == "skills",
            EntityEmbedding.embedding_model == embedding_model,
            EntityEmbedding.entity_id.in_(candidate_ids),
        )
    ).scalars().all()
    return set(rows)


def _score_mode(
    session: Session, candidate_ids: list[int], ground_truth: dict[int, set[int]],
    k: int, mode: str, embedding_model: str, stage1_top_n: int,
) -> list[dict]:
    per_candidate = []
    for candidate_id in candidate_ids:
        if mode == "rule_based":
            ranked_job_ids = [r.entity_id for r in rank_jobs_for_candidate(session, candidate_id, top_k=k)]
        else:
            ranked_job_ids = [
                r.job_id for r in rank_jobs_for_candidate_semantic(
                    session, candidate_id, top_k=k, embedding_model=embedding_model,
                    stage1_top_n=stage1_top_n, mode=mode,
                )
            ]

        relevant = ground_truth[candidate_id]
        per_candidate.append({
            "precision_at_k": precision_at_k(ranked_job_ids, relevant, k),
            "recall_at_k": recall_at_k(ranked_job_ids, relevant, k),
            "ndcg_at_k": ndcg_at_k(ranked_job_ids, relevant, k),
            "reciprocal_rank": reciprocal_rank(ranked_job_ids, relevant),
        })
    return per_candidate


def _avg(per_candidate: list[dict], key: str) -> float:
    n = len(per_candidate)
    return round(sum(r[key] for r in per_candidate) / n, 4) if n else 0.0


def _log_to_mlflow(run: EmbeddingEvalResult) -> None:
    os.environ.setdefault("MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT", "true")
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
        with mlflow.start_run(run_name=f"embedding-eval-{run.id}"):
            mlflow.log_params({
                "embedding_model": run.embedding_model,
                "scoring_mode": run.scoring_mode,
                "sample_size": run.sample_size,
            })
            metrics = {
                "precision_at_k": float(run.precision_at_k_avg),
                "recall_at_k": float(run.recall_at_k_avg),
                "ndcg_at_k": float(run.ndcg_at_k_avg),
                "mrr": float(run.mrr_avg),
            }
            if run.near_miss_accuracy is not None:
                metrics["near_miss_accuracy"] = float(run.near_miss_accuracy)
            mlflow.log_metrics(metrics)
    except Exception:
        logger.warning(
            "MLflow logging failed (tracking server unreachable?) — "
            "embedding eval run still persisted to Postgres.", exc_info=True
        )


def run_embedding_eval(
    session: Session,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    k: int = DEFAULT_K,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    stage1_top_n: int = 200,
) -> list[EmbeddingEvalResult]:
    ground_truth = build_ground_truth(session)
    all_candidate_ids = sorted(ground_truth.keys())
    with_embeddings = _candidates_with_embeddings(session, all_candidate_ids, embedding_model)
    candidate_ids = [cid for cid in all_candidate_ids if cid in with_embeddings][:sample_size]

    if not candidate_ids:
        raise ValueError(
            "No candidates with both ground truth and computed embeddings — "
            "run `python -m app.embeddings.compute_embeddings` first"
        )

    near_miss_accuracy, near_miss_detail = score_near_miss_pairs(embedding_model)

    runs = []
    for mode in ["rule_based", "blended", "embeddings_only"]:
        per_candidate = _score_mode(
            session, candidate_ids, ground_truth, k, mode, embedding_model, stage1_top_n
        )
        run = EmbeddingEvalResult(
            run_at=datetime.now(timezone.utc),
            config_label=f"{mode}:{embedding_model}",
            embedding_model=embedding_model,
            scoring_mode=mode,
            sample_size=len(per_candidate),
            precision_at_k_avg=_avg(per_candidate, "precision_at_k"),
            recall_at_k_avg=_avg(per_candidate, "recall_at_k"),
            ndcg_at_k_avg=_avg(per_candidate, "ndcg_at_k"),
            mrr_avg=_avg(per_candidate, "reciprocal_rank"),
            near_miss_accuracy=round(near_miss_accuracy, 4) if mode != "rule_based" else None,
            detail={"near_miss_pairs": near_miss_detail} if mode != "rule_based" else {},
        )
        session.add(run)
        session.flush()
        session.refresh(run)
        _log_to_mlflow(run)
        runs.append(run)

    session.commit()
    return runs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--stage1-top-n", type=int, default=200)
    args = parser.parse_args()

    db_session = SessionLocal()
    eval_runs = run_embedding_eval(
        db_session, embedding_model=args.embedding_model, k=args.k,
        sample_size=args.sample_size, stage1_top_n=args.stage1_top_n,
    )
    print(f"Embedding eval, model={args.embedding_model}, sample_size={eval_runs[0].sample_size}")
    for run in eval_runs:
        near_miss = f" near_miss_acc={run.near_miss_accuracy}" if run.near_miss_accuracy is not None else ""
        print(
            f"  [{run.scoring_mode:16}] P@{args.k}={run.precision_at_k_avg} "
            f"R@{args.k}={run.recall_at_k_avg} NDCG@{args.k}={run.ndcg_at_k_avg} "
            f"MRR={run.mrr_avg}{near_miss}"
        )
    db_session.close()
