"""Evaluation runner for Use Case C's skill-extraction pipeline (Phase 16).

Run with: python -m app.extraction.run_extraction_eval
  [--llm-model X] [--embedding-model Y] [--threshold T] [--sample-size N]

Tracking split identical to app/evaluation/run_evaluation.py: MLflow tracks
run-level params (model/threshold config) and aggregate metrics — a
browsable, comparable history across configs, which is the whole point of
this harness ("try different models, see what scores better"). Postgres
(extraction_example_results) keeps per-example detail for drill-down.
MLflow logging is best-effort, same as Phase 4: a tracking-server outage
doesn't fail the run, it only skips the MLflow log.
"""
import argparse
import logging
import os
from datetime import datetime, timezone

import mlflow

from app.db import SessionLocal
from app.extraction.golden_dataset import load_generator_injected, load_hand_authored
from app.extraction.metrics import requirement_level_accuracy, set_f1, set_precision, set_recall
from app.extraction.models import ExtractionExampleResult, ExtractionRun
from app.extraction.pipeline import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LLM_MODEL,
    DEFAULT_SIMILARITY_THRESHOLD,
    PROMPT_VERSION,
    extract_skills,
)

logger = logging.getLogger(__name__)

MLFLOW_EXPERIMENT_NAME = "jme-skill-extraction"
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
# Modest sample of the generator-injected set, not the full 1,000 jobs — each
# example costs a real LLM call plus one embedding call per extracted
# phrase, so a full-population run is minutes, not seconds (see
# docs/skill-extraction.md known limitations). Scale up once that cost is
# worth paying for a specific comparison, the same judgment call Phase 4
# made before the Phase 9 N+1 fix.
DEFAULT_SAMPLE_SIZE = 30


def _log_to_mlflow(run: ExtractionRun) -> None:
    os.environ.setdefault("MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT", "true")
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
        with mlflow.start_run(run_name=f"extraction-run-{run.id}"):
            mlflow.log_params({
                "llm_model": run.llm_model,
                "embedding_model": run.embedding_model,
                "similarity_threshold": float(run.similarity_threshold),
                "prompt_version": run.prompt_version,
                "sample_size": run.sample_size,
            })
            mlflow.log_metrics({
                "precision": float(run.precision_avg),
                "recall": float(run.recall_avg),
                "f1": float(run.f1_avg),
                "requirement_level_accuracy": float(run.requirement_level_accuracy_avg),
                "unmatched_phrase_rate": float(run.unmatched_phrase_rate_avg),
            })
    except Exception:
        logger.warning(
            "MLflow logging failed (tracking server unreachable?) — "
            "extraction run still persisted to Postgres.", exc_info=True
        )


def run_extraction_eval(
    session,
    llm_model: str = DEFAULT_LLM_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> tuple[ExtractionRun, list[dict]]:
    examples = load_generator_injected(session, sample_size=sample_size) + load_hand_authored(session)

    per_example = []
    for ex in examples:
        result = extract_skills(
            ex["text"], session,
            llm_model=llm_model, embedding_model=embedding_model,
            similarity_threshold=similarity_threshold,
        )
        predicted_ids = {m["skill_id"] for m in result["matches"]}
        expected_ids = {e["skill_id"] for e in ex["expected"]}
        predicted_levels = {m["skill_id"]: m["requirement_level"] for m in result["matches"]}
        expected_levels = {e["skill_id"]: e["requirement_level"] for e in ex["expected"]}
        true_positive_ids = predicted_ids & expected_ids

        total_phrases = len(result["matches"]) + len(result["unmatched"])
        unmatched_rate = len(result["unmatched"]) / total_phrases if total_phrases else 0.0

        per_example.append({
            "example_id": ex["example_id"],
            "source": ex["source"],
            "precision": set_precision(predicted_ids, expected_ids),
            "recall": set_recall(predicted_ids, expected_ids),
            "f1": set_f1(predicted_ids, expected_ids),
            "requirement_level_accuracy": requirement_level_accuracy(
                predicted_levels, expected_levels, true_positive_ids
            ),
            "unmatched_phrase_rate": unmatched_rate,
            "expected_skill_ids": sorted(expected_ids),
            "predicted_skill_ids": sorted(predicted_ids),
            "unmatched_phrases": [u["phrase"] for u in result["unmatched"]],
        })

    n = len(per_example)
    rla_values = [
        r["requirement_level_accuracy"] for r in per_example
        if r["requirement_level_accuracy"] is not None
    ]

    run = ExtractionRun(
        run_at=datetime.now(timezone.utc),
        llm_model=llm_model,
        embedding_model=embedding_model,
        similarity_threshold=similarity_threshold,
        prompt_version=PROMPT_VERSION,
        sample_size=n,
        precision_avg=round(sum(r["precision"] for r in per_example) / n, 4) if n else 0.0,
        recall_avg=round(sum(r["recall"] for r in per_example) / n, 4) if n else 0.0,
        f1_avg=round(sum(r["f1"] for r in per_example) / n, 4) if n else 0.0,
        requirement_level_accuracy_avg=(
            round(sum(rla_values) / len(rla_values), 4) if rla_values else 0.0
        ),
        unmatched_phrase_rate_avg=(
            round(sum(r["unmatched_phrase_rate"] for r in per_example) / n, 4) if n else 0.0
        ),
    )
    session.add(run)
    session.flush()

    for r in per_example:
        session.add(ExtractionExampleResult(
            run_id=run.id,
            example_id=r["example_id"],
            source=r["source"],
            precision=round(r["precision"], 4),
            recall=round(r["recall"], 4),
            f1=round(r["f1"], 4),
            requirement_level_accuracy=(
                round(r["requirement_level_accuracy"], 4)
                if r["requirement_level_accuracy"] is not None else None
            ),
            expected_skill_ids=r["expected_skill_ids"],
            predicted_skill_ids=r["predicted_skill_ids"],
            unmatched_phrases=r["unmatched_phrases"],
        ))

    session.commit()
    session.refresh(run)
    _log_to_mlflow(run)
    return run, per_example


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    args = parser.parse_args()

    db_session = SessionLocal()
    extraction_run, _ = run_extraction_eval(
        db_session,
        llm_model=args.llm_model,
        embedding_model=args.embedding_model,
        similarity_threshold=args.threshold,
        sample_size=args.sample_size,
    )
    print(f"Extraction eval run #{extraction_run.id} at {extraction_run.run_at}")
    print(
        f"  llm_model={extraction_run.llm_model} "
        f"embedding_model={extraction_run.embedding_model} "
        f"threshold={extraction_run.similarity_threshold}"
    )
    print(f"  sample_size={extraction_run.sample_size}")
    print(f"  Precision: {extraction_run.precision_avg}")
    print(f"  Recall:    {extraction_run.recall_avg}")
    print(f"  F1:        {extraction_run.f1_avg}")
    print(f"  Requirement-level accuracy: {extraction_run.requirement_level_accuracy_avg}")
    print(f"  Unmatched-phrase rate:      {extraction_run.unmatched_phrase_rate_avg}")
    db_session.close()
