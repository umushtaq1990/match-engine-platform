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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import mlflow

from app.common.config import settings
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
from app.extraction.skill_cache import get_reference_embeddings

logger = logging.getLogger(__name__)

MLFLOW_EXPERIMENT_NAME = "jme-skill-extraction"
MLFLOW_TRACKING_URI = settings.mlflow_tracking_uri
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
        # "/" in the model name means an HF repo path -- served via vLLM in
        # this project (app/golden/vllm_client.py), vs an Ollama tag like
        # "qwen2.5:1.5b" (no slash). A model-only run_name made every run in
        # the list look identical ("extraction-run-N") -- unreadable once
        # more than a couple of configs were being compared side by side.
        backend = "vllm" if "/" in run.llm_model else "ollama"
        model_short = run.llm_model.split("/")[-1]
        run_name = f"{model_short} | thr={run.similarity_threshold} | {run.prompt_version} | {backend}"
        description = (
            f"**Model**: `{run.llm_model}` ({backend})\n\n"
            f"**Embedding model**: `{run.embedding_model}`\n\n"
            f"**Similarity threshold**: {run.similarity_threshold}\n\n"
            f"**Prompt version**: {run.prompt_version}\n\n"
            f"**Sample size**: {run.sample_size}\n\n"
            f"**Results**: Precision {run.precision_avg} / Recall {run.recall_avg} / "
            f"F1 {run.f1_avg}"
        )
        with mlflow.start_run(run_name=run_name):
            mlflow.set_tag("mlflow.note.content", description)
            mlflow.set_tags({
                "backend": backend,
                "model_family": model_short.split("-")[0].split("_")[0],
            })
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


def _extract_one(
    ex: dict, llm_model: str, embedding_model: str, similarity_threshold: float, session=None,
) -> dict:
    logger.info(
        "extracting skills", extra={"example_id": ex["example_id"], "source": ex["source"]}
    )
    try:
        result = extract_skills(
            ex["text"], session,
            llm_model=llm_model, embedding_model=embedding_model,
            similarity_threshold=similarity_threshold,
        )
    except Exception:
        logger.warning(
            "extraction failed", exc_info=True,
            extra={"example_id": ex["example_id"], "source": ex["source"]},
        )
        raise
    predicted_ids = {m["skill_id"] for m in result["matches"]}
    expected_ids = {e["skill_id"] for e in ex["expected"]}
    predicted_levels = {m["skill_id"]: m["requirement_level"] for m in result["matches"]}
    expected_levels = {e["skill_id"]: e["requirement_level"] for e in ex["expected"]}
    true_positive_ids = predicted_ids & expected_ids

    total_phrases = len(result["matches"]) + len(result["unmatched"])
    unmatched_rate = len(result["unmatched"]) / total_phrases if total_phrases else 0.0

    # Best-effort, same reasoning as app/extraction/routes.py's identical
    # capture for extraction_calls -- full traceability means every golden-
    # eval example should be able to point at its own MLflow trace too, not
    # just production calls. get_last_active_trace_id(), not
    # get_current_active_span(): extract_skills()'s own @mlflow.trace span
    # has already closed by the time control returns here. Mlflow's tracing
    # context is thread-local, so this stays correct per-example even when
    # max_workers>1 runs extract_skills() calls concurrently across threads
    # (confirmed live, not assumed -- see golden-evaluation-dataset.md).
    mlflow_trace_id = None
    try:
        mlflow_trace_id = mlflow.get_last_active_trace_id()
    except Exception:
        pass

    return {
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
        "extracted_phrases": result["extracted"],
        "match_detail": result["matches"],
        "unmatched_detail": result["unmatched"],
        "mlflow_trace_id": mlflow_trace_id,
    }


def run_extraction_eval(
    session,
    llm_model: str = DEFAULT_LLM_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    examples: list[dict] | None = None,
    max_workers: int = 1,
) -> tuple[ExtractionRun, list[dict]]:
    """`examples`, when given, replaces the default generator-injected +
    hand-authored dataset entirely -- used by app/golden/run_extraction_eval.py
    to score the same persistence/metrics/MLflow pipeline against the 500
    golden candidates' real natural-prose CVs/job postings instead.

    `max_workers` > 1 runs extract_skills() calls concurrently via a thread
    pool -- safe ONLY because extract_skills()'s one DB touch
    (skill_cache.get_reference_embeddings) is pre-warmed synchronously
    below and memoizes in a module-level dict keyed by embedding_model, so
    every worker thread's extract_skills() call hits that cache instead of
    the shared `session` -- no concurrent session use ever actually
    happens. This is a real invariant, not an assumption: if extract_skills
    ever starts using `session` for anything beyond that one cache lookup,
    this parallel path stops being safe and needs a per-thread session
    instead. Each example is otherwise independent (its own text, its own
    metrics), so ordering doesn't matter for the aggregate.
    """
    if examples is None:
        examples = load_generator_injected(session, sample_size=sample_size) + load_hand_authored(session)

    if max_workers > 1:
        get_reference_embeddings(session, embedding_model)  # pre-warm before any thread touches it
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            per_example = list(pool.map(
                lambda ex: _extract_one(ex, llm_model, embedding_model, similarity_threshold),
                examples,
            ))
    else:
        per_example = [
            _extract_one(ex, llm_model, embedding_model, similarity_threshold, session=session)
            for ex in examples
        ]

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
            extracted_phrases=r["extracted_phrases"],
            match_detail=r["match_detail"],
            unmatched_detail=r["unmatched_detail"],
            mlflow_trace_id=r.get("mlflow_trace_id"),
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
