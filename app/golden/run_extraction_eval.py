"""Extraction eval against the golden dataset's real natural-prose CVs and
job postings -- same persistence/metrics/MLflow pipeline as
app/extraction/run_extraction_eval.py (reused via its additive `examples`
param), different input: real LLM-written text instead of
app/extraction/golden_dataset.py's generator-injected (templated,
ground-truth-visible-in-the-text-by-construction) or hand-authored
snippets. Ground truth is the candidate's real CandidateSkill rows / the
job's real JobSkill rows -- already persisted by generate_cvs.py/
generate_jobs.py, nothing new to store here.

CVs are stored as PDFs (document_storage.py), not raw text, and this
project has no existing CV-to-text pipeline (a documented, deliberately
deferred gap -- see docs/architecture's issue #10 review) -- this script
parses the real uploaded PDF bytes back to text via pypdf at eval time,
rather than caching the pre-render prose separately, so this actually
exercises "real CV bytes in, skills out," not a shortcut around it.

Run with: docker compose run --rm golden-extraction-eval [--limit N]
"""
import argparse
import logging
import time

import httpx
from pypdf import PdfReader
from sqlalchemy import select

import app.extraction.pipeline as pipeline
from app.common.logging import setup_logging
from app.db import SessionLocal
from app.extraction.document_storage import download_cv
from app.extraction.models import CandidateDocument, ExtractionExampleResult
from app.extraction.ollama_client import generate as _ollama_generate
from app.extraction.run_extraction_eval import run_extraction_eval
from app.golden import vllm_client
from app.golden.generate_cvs import GOLDEN_FILENAME
from app.golden.select import select_golden_candidate_ids
from app.ingestion.source_models import CandidateSkill, Job, JobSkill, ProficiencyLevel

logger = logging.getLogger(__name__)

# This host has hit isolated Ollama timeouts roughly every 1-2 minutes
# during live eval runs (confirmed via docker logs -- not rare long-tail
# latency, frequent enough that even re-running the whole ~200-call batch
# fails every time). extract_skills() calls ollama_client.generate()
# directly with no retry, unlike generate_cvs.py/generate_jobs.py which
# use llm_retry.generate_with_retry -- patch the same per-call retry onto
# pipeline.py's bound reference here, scoped to this eval script only, so
# one slow call doesn't void the whole batch. Works for either backend's
# generate() function since both share the same (model, prompt,
# response_format, temperature) signature.
def _make_generate_with_retry(backend_generate, attempts=3):
    def _generate_with_retry(model, prompt, response_format=None, temperature=None):
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return backend_generate(
                    model, prompt, response_format=response_format, temperature=temperature,
                )
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                last_exc = exc
                logger.warning(
                    "%s call failed (attempt %s/%s): %s",
                    backend_generate.__module__, attempt, attempts, exc,
                )
                if attempt < attempts:
                    time.sleep(5)
        raise last_exc
    return _generate_with_retry


DEFAULT_SAMPLE_SIZE = 50


def _pdf_to_text(pdf_bytes: bytes) -> str:
    from io import BytesIO

    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _cv_examples(session, candidate_ids: list[int]) -> list[dict]:
    golden_doc_candidate_ids = {
        row[0] for row in session.execute(
            select(CandidateDocument.candidate_id).where(
                CandidateDocument.candidate_id.in_(candidate_ids),
                CandidateDocument.original_filename == GOLDEN_FILENAME,
            )
        ).all()
    }

    examples = []
    for candidate_id in candidate_ids:
        if candidate_id not in golden_doc_candidate_ids:
            continue
        text = _pdf_to_text(download_cv(candidate_id))
        expected = [
            {
                "skill_id": skill_id,
                # A CV has no required/preferred concept -- proficiency
                # intermediate+ counts as the "should be matched" positive
                # set, mirroring generate_jobs.py's own skill->requirement
                # mapping so the two example types stay comparable.
                "requirement_level": (
                    "preferred" if proficiency == ProficiencyLevel.beginner else "required"
                ),
            }
            for skill_id, proficiency in session.execute(
                select(CandidateSkill.skill_id, CandidateSkill.proficiency)
                .where(CandidateSkill.candidate_id == candidate_id)
            ).all()
        ]
        examples.append({
            "example_id": f"golden_cv:{candidate_id}", "source": "golden_cv",
            "text": text, "expected": expected,
        })
    return examples


def _job_examples(session, candidate_ids: list[int]) -> list[dict]:
    from app.golden.models import GoldenPair

    job_ids = session.execute(
        select(GoldenPair.job_id).where(GoldenPair.candidate_id.in_(candidate_ids))
    ).scalars().all()
    return _job_examples_by_job_ids(session, job_ids)


def _job_examples_by_job_ids(session, job_ids: list[int]) -> list[dict]:
    examples = []
    for job_id in job_ids:
        job = session.get(Job, job_id)
        job_skills = session.execute(select(JobSkill).where(JobSkill.job_id == job_id)).scalars().all()
        examples.append({
            "example_id": f"golden_job:{job_id}", "source": "golden_job",
            "text": job.description_raw,
            "expected": [
                {"skill_id": js.skill_id, "requirement_level": js.requirement_level.value}
                for js in job_skills
            ],
        })
    return examples


def _hard_case_example_ids(session, compare_run_ids: list[int], f1_below: float) -> list[str]:
    """Finds the exact examples where every one of `compare_run_ids` (e.g.
    the clean, chat-completions-based full-dataset runs for the 3 models
    already tested) scored below `f1_below` -- used to target only the
    genuinely hard cases with a bigger/different model, instead of
    re-running the full ~2000-example dataset (confirmed live: only
    ~95/2000 examples qualify at f1_below=0.5, a ~95% reduction in the
    CPU-bound work needed for an otherwise multi-hour-per-model comparison)."""
    from sqlalchemy import func

    rows = session.execute(
        select(ExtractionExampleResult.example_id, func.max(ExtractionExampleResult.f1))
        .where(ExtractionExampleResult.run_id.in_(compare_run_ids))
        .group_by(ExtractionExampleResult.example_id)
        .having(func.max(ExtractionExampleResult.f1) < f1_below)
    ).all()
    return [example_id for example_id, _best_f1 in rows]


def _examples_by_ids(session, example_ids: list[str]) -> list[dict]:
    """Builds the exact example set for a given list of example_id strings
    ("golden_cv:<candidate_id>" / "golden_job:<job_id>") -- used by the
    hard-case targeting above, where we want precisely these examples, not
    every example belonging to the candidates they happen to come from."""
    cv_candidate_ids = []
    job_ids = []
    for example_id in example_ids:
        kind, _, raw_id = example_id.partition(":")
        if kind == "golden_cv":
            cv_candidate_ids.append(int(raw_id))
        elif kind == "golden_job":
            job_ids.append(int(raw_id))
    return _cv_examples(session, cv_candidate_ids) + _job_examples_by_job_ids(session, job_ids)


def main(
    limit: int | None, offset: int = 0, workers: int = 1,
    llm_model: str = "qwen2.5:1.5b", backend: str = "ollama",
    hard_cases_f1_below: float | None = None, compare_run_ids: list[int] | None = None,
) -> None:
    if backend == "vllm":
        pipeline.generate = _make_generate_with_retry(vllm_client.generate)
        llm_model = vllm_client.VLLM_MODEL  # vLLM serves exactly one model per process
    else:
        pipeline.generate = _make_generate_with_retry(_ollama_generate)
    logger.info("backend selected", extra={"backend": backend, "llm_model": llm_model})

    session = SessionLocal()
    try:
        if hard_cases_f1_below is not None:
            hard_ids = _hard_case_example_ids(session, compare_run_ids, hard_cases_f1_below)
            logger.info(
                "targeting hard cases only", extra={
                    "f1_below": hard_cases_f1_below, "compare_run_ids": compare_run_ids,
                    "hard_example_count": len(hard_ids),
                },
            )
            examples = _examples_by_ids(session, hard_ids)
        else:
            golden_ids = select_golden_candidate_ids(session)
            # select_golden_candidate_ids is deterministic (same 500 ids, same
            # order, every call) -- an --offset lets repeated small runs cover
            # fresh candidates each time (e.g. --limit 5 --offset 5 for the
            # next 5) instead of re-scoring the same first N every time. Each
            # run still writes its own ExtractionRun row (no built-in dedup
            # against a prior run's results), so distinct offsets don't
            # overwrite each other -- a later full run (e.g. --limit 500, no
            # offset) re-scores everything fresh in one combined run instead.
            golden_ids = golden_ids[offset:]
            if limit is not None:
                golden_ids = golden_ids[:limit]
            examples = _cv_examples(session, golden_ids) + _job_examples(session, golden_ids)
        if not examples:
            logger.info("No golden CVs/jobs found yet -- run golden-cvs and golden-jobs first.")
            return
        logger.info("starting golden extraction eval", extra={"example_count": len(examples)})

        # Host infra has repeatedly hit isolated Ollama timeouts/DNS blips
        # mid-run (confirmed live across several attempts); a single one
        # kills the whole batch of ~200 extraction calls with no partial
        # credit, so retry the whole eval a few times rather than lose it.
        last_exc: Exception | None = None
        run = None
        for attempt in range(1, 4):
            try:
                run, _ = run_extraction_eval(
                    session, examples=examples, max_workers=workers, llm_model=llm_model
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                session.rollback()
                logger.warning(
                    "extraction eval attempt failed", exc_info=True,
                    extra={"attempt": attempt, "max_attempts": 3},
                )
                if attempt < 3:
                    time.sleep(10)
        if run is None:
            raise last_exc
        logger.info(
            "golden extraction eval complete",
            extra={
                "run_id": run.id, "sample_size": run.sample_size,
                "precision": run.precision_avg, "recall": run.recall_avg, "f1": run.f1_avg,
                "requirement_level_accuracy": run.requirement_level_accuracy_avg,
                "unmatched_phrase_rate": run.unmatched_phrase_rate_avg,
            },
        )
        print(f"Golden extraction eval run #{run.id} at {run.run_at}")
        print(f"  sample_size={run.sample_size} (CVs + golden job postings)")
        print(f"  Precision: {run.precision_avg}")
        print(f"  Recall:    {run.recall_avg}")
        print(f"  F1:        {run.f1_avg}")
        print(f"  Requirement-level accuracy: {run.requirement_level_accuracy_avg}")
        print(f"  Unmatched-phrase rate:      {run.unmatched_phrase_rate_avg}")
    finally:
        session.close()


if __name__ == "__main__":
    setup_logging("golden-extraction-eval")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_SAMPLE_SIZE, help="Golden candidates to sample from"
    )
    parser.add_argument(
        "--offset", type=int, default=0,
        help="Skip the first N golden candidates (by the same deterministic ordering "
             "select_golden_candidate_ids always returns) -- use with --limit to cover "
             "a fresh batch each run instead of re-testing the same candidates.",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Run this many extract_skills() calls concurrently (matches OLLAMA_NUM_PARALLEL "
             "in docker-compose.yml -- more client workers than the server will actually run "
             "in parallel just queue, no extra speedup).",
    )
    parser.add_argument(
        "--model", type=str, default="qwen2.5:1.5b",
        help="Ollama model tag to use for extraction (must already be pulled) -- lets this "
             "script compare quantization levels/models directly via the same eval harness. "
             "Ignored when --backend vllm (vLLM serves exactly one model per process).",
    )
    parser.add_argument(
        "--backend", choices=["ollama", "vllm"], default="ollama",
        help="Which server to call for extraction generation -- embeddings always go through "
             "Ollama (nomic-embed-text) either way, vLLM here only serves the completion model.",
    )
    parser.add_argument(
        "--hard-cases-f1-below", type=float, default=None,
        help="Instead of the normal --limit/--offset candidate selection, targets only the "
             "examples where every run in --compare-run-ids scored below this F1 -- lets a "
             "slow (e.g. CPU) bigger-model comparison run against just the cases existing "
             "models struggled on, instead of the full ~2000-example dataset.",
    )
    parser.add_argument(
        "--compare-run-ids", type=str, default=None,
        help="Comma-separated ExtractionRun ids to compare against for --hard-cases-f1-below "
             "(e.g. '29,32,34' for the three already-tested model configs).",
    )
    args = parser.parse_args()
    compare_run_ids = (
        [int(x) for x in args.compare_run_ids.split(",")] if args.compare_run_ids else None
    )
    main(
        limit=args.limit, offset=args.offset, workers=args.workers,
        llm_model=args.model, backend=args.backend,
        hard_cases_f1_below=args.hard_cases_f1_below, compare_run_ids=compare_run_ids,
    )
