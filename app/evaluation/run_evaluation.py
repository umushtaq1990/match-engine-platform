"""Evaluation runner: scores the current matching engine's ranking quality
against the synthetic ground truth (ground_truth.py), using standard IR
metrics (metrics.py), and persists results so future scoring-weight changes
can be compared against this baseline (brief: "Compare alternative scoring
weights and evaluate ranking quality").

Run with: python -m app.evaluation.run_evaluation

Sample size is capped (default 50), not the full candidate population —
documented, measured reason: rank_jobs_for_candidate costs ~2s/candidate due
to the known N+1 query limitation in app/matching/profiles.py (see
docs/matching-engine.md). Full-population evaluation is a Phase 9 follow-up
once that's optimized, not attempted here.

Tracking split (deliberate, not duplicated): MLflow tracks run-level
params/aggregate metrics (a browsable, comparable experiment history across
scoring-weight configs). Postgres (evaluation_candidate_results) keeps the
per-candidate detail rows, for drill-down and a future dashboard — a
different consumer/granularity, not the same data twice. MLflow logging is
best-effort: if the tracking server is unreachable, the run still completes
and persists to Postgres; only the MLflow log is skipped (with a warning).
"""
import logging
import os
from datetime import datetime, timezone

import mlflow
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.evaluation.eval_models import EvaluationCandidateResult, EvaluationRun
from app.evaluation.ground_truth import build_ground_truth
from app.evaluation.metrics import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from app.ingestion.source_models import Candidate
from app.matching.rank import rank_jobs_for_candidate
from app.matching.scoring import WEIGHTS

logger = logging.getLogger(__name__)

DEFAULT_K = 10
DEFAULT_SAMPLE_SIZE = 50
MLFLOW_EXPERIMENT_NAME = "jme-matching"
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")


def _config_label() -> str:
    return "weights:" + ",".join(f"{k}={v}" for k, v in WEIGHTS.items())


def _log_to_mlflow(run: EvaluationRun) -> None:
    # Avoids a UnicodeEncodeError on Windows consoles (cp1252 can't encode the
    # emoji MLflow prints for "view run at: <url>") — official suppression
    # flag, not a workaround; the console confirmation isn't needed here since
    # results are already printed by this script below.
    os.environ.setdefault("MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT", "true")
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
        with mlflow.start_run(run_name=f"eval-run-{run.id}"):
            mlflow.log_params({**WEIGHTS, "k": run.k, "sample_size": run.sample_size})
            mlflow.log_metrics({
                "precision_at_k": float(run.precision_at_k_avg),
                "recall_at_k": float(run.recall_at_k_avg),
                "ndcg_at_k": float(run.ndcg_at_k_avg),
                "mrr": float(run.mrr_avg),
            })
    except Exception:
        logger.warning(
            "MLflow logging failed (tracking server unreachable?) — "
            "evaluation run still persisted to Postgres.", exc_info=True
        )


def run_evaluation(
    session: Session, k: int = DEFAULT_K, sample_size: int = DEFAULT_SAMPLE_SIZE
) -> tuple[EvaluationRun, list[dict]]:
    ground_truth = build_ground_truth(session)
    candidate_ids = sorted(ground_truth.keys())[:sample_size]

    per_candidate = []
    for candidate_id in candidate_ids:
        ranked = rank_jobs_for_candidate(session, candidate_id, top_k=k)
        ranked_job_ids = [r.entity_id for r in ranked]
        relevant = ground_truth[candidate_id]

        per_candidate.append({
            "candidate_id": candidate_id,
            "precision_at_k": precision_at_k(ranked_job_ids, relevant, k),
            "recall_at_k": recall_at_k(ranked_job_ids, relevant, k),
            "ndcg_at_k": ndcg_at_k(ranked_job_ids, relevant, k),
            "reciprocal_rank": reciprocal_rank(ranked_job_ids, relevant),
        })

    n = len(per_candidate)
    total_candidates = session.execute(select(func.count()).select_from(Candidate)).scalar_one()
    run = EvaluationRun(
        run_at=datetime.now(timezone.utc),
        k=k,
        sample_size=n,
        excluded_no_ground_truth=total_candidates - len(ground_truth),
        config_label=_config_label(),
        precision_at_k_avg=round(sum(r["precision_at_k"] for r in per_candidate) / n, 4) if n else 0.0,
        recall_at_k_avg=round(sum(r["recall_at_k"] for r in per_candidate) / n, 4) if n else 0.0,
        ndcg_at_k_avg=round(sum(r["ndcg_at_k"] for r in per_candidate) / n, 4) if n else 0.0,
        mrr_avg=round(sum(r["reciprocal_rank"] for r in per_candidate) / n, 4) if n else 0.0,
    )
    session.add(run)
    session.flush()

    for r in per_candidate:
        session.add(
            EvaluationCandidateResult(
                run_id=run.id,
                candidate_id=r["candidate_id"],
                precision_at_k=round(r["precision_at_k"], 4),
                recall_at_k=round(r["recall_at_k"], 4),
                ndcg_at_k=round(r["ndcg_at_k"], 4),
                reciprocal_rank=round(r["reciprocal_rank"], 4),
            )
        )

    session.commit()
    session.refresh(run)
    _log_to_mlflow(run)
    return run, per_candidate


if __name__ == "__main__":
    session = SessionLocal()
    run, _ = run_evaluation(session)
    print(f"Evaluation run #{run.id} at {run.run_at}")
    print(f"  k={run.k}, sample_size={run.sample_size}, "
          f"excluded_no_ground_truth={run.excluded_no_ground_truth}")
    print(f"  config: {run.config_label}")
    print(f"  Precision@{run.k}: {run.precision_at_k_avg}")
    print(f"  Recall@{run.k}:    {run.recall_at_k_avg}")
    print(f"  NDCG@{run.k}:      {run.ndcg_at_k_avg}")
    print(f"  MRR:          {run.mrr_avg}")
    session.close()
