"""golden-dataset v2's deferred follow-up, now built: does the confidence-
scoring review-queue mechanism (app/extraction/pipeline.py's
_document_confidence, _REVIEW_THRESHOLD) actually catch the deliberately
injected, unfindable skills from app/golden/inject_noise.py?

Also reports *corrected* precision/recall/F1 for the run -- since we know
exactly which skill was injected into which example, stripping it from
that one example's expected_skill_ids before rescoring gives an honest
read of the model's real extraction quality, undeflated by ground truth
we know is deliberately wrong. Confirmed live: run 38's raw F1 (0.8273,
`extraction_runs.f1_avg`, computed against the uncorrected -- deliberately
corrupted -- ground truth) understates true quality by about half a point
(corrected F1 0.8337) -- a real, if modest, effect, not assumed. This
corrected number is NOT written back to `extraction_runs` -- the raw
metrics there stay as originally scored (that's the whole point of
running this check against them), this is a separate, repeatable report
on top of already-persisted data.

No new extraction calls are made here -- document_confidence is computed
retroactively from each example's already-persisted match_detail/
unmatched_detail (ExtractionExampleResult), reusing pipeline.py's own
formula directly rather than reimplementing it, so this measures exactly
the same heuristic production actually uses.

Run with: docker compose run --rm golden-analyze-noise [--run-id N]
"""
import argparse

from sqlalchemy import select

from app.db import SessionLocal
from app.extraction.metrics import set_f1, set_precision, set_recall
from app.extraction.models import ExtractionExampleResult, ExtractionRun
from app.extraction.pipeline import _REVIEW_THRESHOLD, _document_confidence
from app.golden.models import GoldenInjectedNoise


def _pick_default_run_id(session) -> int:
    row = session.execute(select(ExtractionRun.id).order_by(ExtractionRun.f1_avg.desc()).limit(1)).first()
    if row is None:
        raise SystemExit("No extraction_runs found -- pass --run-id explicitly.")
    return row[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=None)
    args = parser.parse_args()

    session = SessionLocal()
    try:
        run_id = args.run_id or _pick_default_run_id(session)

        noised_candidate_ids = set(session.execute(
            select(GoldenInjectedNoise.candidate_id).where(GoldenInjectedNoise.candidate_id.isnot(None))
        ).scalars().all())
        noised_job_ids = set(session.execute(
            select(GoldenInjectedNoise.job_id).where(GoldenInjectedNoise.job_id.isnot(None))
        ).scalars().all())
        noise_skill_by_candidate = dict(session.execute(
            select(GoldenInjectedNoise.candidate_id, GoldenInjectedNoise.skill_id)
            .where(GoldenInjectedNoise.candidate_id.isnot(None))
        ).all())
        noise_skill_by_job = dict(session.execute(
            select(GoldenInjectedNoise.job_id, GoldenInjectedNoise.skill_id)
            .where(GoldenInjectedNoise.job_id.isnot(None))
        ).all())

        results = session.execute(
            select(ExtractionExampleResult).where(ExtractionExampleResult.run_id == run_id)
        ).scalars().all()

        noisy_confidences, clean_confidences = [], []
        noisy_flagged = noisy_total = 0
        clean_flagged = clean_total = 0
        false_positive_matches = 0  # injected skill wrongly ended up in match_detail anyway
        orig_p, orig_r, orig_f1 = [], [], []
        corrected_p, corrected_r, corrected_f1 = [], [], []

        for r in results:
            raw_id = int(r.example_id.split(":", 1)[1])
            if r.source == "golden_cv":
                is_noisy = raw_id in noised_candidate_ids
                injected_skill_id = noise_skill_by_candidate.get(raw_id)
            elif r.source == "golden_job":
                is_noisy = raw_id in noised_job_ids
                injected_skill_id = noise_skill_by_job.get(raw_id)
            else:
                continue

            confidence = _document_confidence(r.match_detail or [], r.unmatched_detail or [])
            flagged = confidence < _REVIEW_THRESHOLD

            if is_noisy:
                noisy_total += 1
                noisy_confidences.append(confidence)
                if flagged:
                    noisy_flagged += 1
                matched_ids = {m["skill_id"] for m in (r.match_detail or [])}
                if injected_skill_id in matched_ids:
                    false_positive_matches += 1
            else:
                clean_total += 1
                clean_confidences.append(confidence)
                if flagged:
                    clean_flagged += 1

            predicted = set(r.predicted_skill_ids or [])
            expected = set(r.expected_skill_ids or [])
            orig_p.append(float(r.precision))
            orig_r.append(float(r.recall))
            orig_f1.append(float(r.f1))

            corrected_expected = expected - {injected_skill_id} if is_noisy else expected
            corrected_p.append(set_precision(predicted, corrected_expected))
            corrected_r.append(set_recall(predicted, corrected_expected))
            corrected_f1.append(set_f1(predicted, corrected_expected))

        def _avg(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        print(f"Run {run_id}: {noisy_total} noise-injected examples, {clean_total} clean examples")
        print(f"  Avg document_confidence, noisy: {_avg(noisy_confidences):.4f}")
        print(f"  Avg document_confidence, clean: {_avg(clean_confidences):.4f}")
        print(
            f"  Review-queue detection rate on noisy examples: "
            f"{noisy_flagged}/{noisy_total} ({noisy_flagged / noisy_total:.1%})"
            if noisy_total else "  No noisy examples in this run"
        )
        print(
            f"  Review-queue false-positive rate on clean examples: "
            f"{clean_flagged}/{clean_total} ({clean_flagged / clean_total:.1%})"
            if clean_total else "  No clean examples in this run"
        )
        print(
            f"  Injected skill wrongly appeared in match_detail anyway: "
            f"{false_positive_matches}/{noisy_total}"
        )

        n = len(orig_f1)
        print(f"\n  Corrected metrics (excluding each example's own injected skill, n={n}):")
        print(
            f"    Original  -- precision={_avg(orig_p):.4f} recall={_avg(orig_r):.4f} "
            f"f1={_avg(orig_f1):.4f}  (matches extraction_runs.*_avg for this run)"
        )
        print(
            f"    Corrected -- precision={_avg(corrected_p):.4f} recall={_avg(corrected_r):.4f} "
            f"f1={_avg(corrected_f1):.4f}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
