"""Recommendation eval against the golden dataset: for each of the 500
golden candidates, checks how many of their 3 *known* golden_pairs jobs
(deliberately constructed as near-perfect matches, see
app/golden/generate_jobs.py) actually appear in the matcher's real top-K
output -- precision@3/recall@3 against a known right answer, a much
sharper signal than app/evaluation/ground_truth.py's population-wide
"same occupation somewhere in the top-2000" ground truth.

Calls app/matching/rank.py's rank_jobs_for_candidate directly rather than
modifying app/evaluation/run_evaluation.py -- simpler and more decoupled
than threading a new parameter through the production eval harness for a
need only this golden-set script has.

Run with: docker compose run --rm golden-recommendation-eval [--limit N] [--k K]
"""
import argparse
import logging

from sqlalchemy import select

from app.common.logging import setup_logging
from app.db import SessionLocal
from app.golden.models import GoldenPair
from app.golden.select import select_golden_candidate_ids
from app.matching.rank import rank_jobs_for_candidate

logger = logging.getLogger(__name__)

DEFAULT_K = 10


def main(limit: int | None, k: int) -> None:
    session = SessionLocal()
    try:
        golden_ids = select_golden_candidate_ids(session)
        if limit is not None:
            golden_ids = golden_ids[:limit]

        golden_jobs_by_candidate: dict[int, set[int]] = {}
        rows = session.execute(
            select(GoldenPair.candidate_id, GoldenPair.job_id).where(GoldenPair.candidate_id.in_(golden_ids))
        ).all()
        for candidate_id, job_id in rows:
            golden_jobs_by_candidate.setdefault(candidate_id, set()).add(job_id)

        per_candidate = []
        for candidate_id in golden_ids:
            true_jobs = golden_jobs_by_candidate.get(candidate_id)
            if not true_jobs:
                continue

            logger.info("ranking jobs for candidate", extra={"candidate_id": candidate_id})
            ranked = rank_jobs_for_candidate(session, candidate_id, top_k=k)
            ranked_job_ids = [r.entity_id for r in ranked]
            found = [jid for jid in ranked_job_ids if jid in true_jobs]
            ranks = {jid: ranked_job_ids.index(jid) + 1 for jid in found}

            per_candidate.append({
                "candidate_id": candidate_id,
                "n_found": len(found),
                "n_true": len(true_jobs),
                "precision_at_k": len(found) / k,
                "recall_at_k": len(found) / len(true_jobs),
                "ranks_found": ranks,
            })

        n = len(per_candidate)
        if n == 0:
            print("No golden pairs found yet -- run golden-jobs first.")
            return

        avg_precision = sum(r["precision_at_k"] for r in per_candidate) / n
        avg_recall = sum(r["recall_at_k"] for r in per_candidate) / n
        all_found = sum(1 for r in per_candidate if r["n_found"] == r["n_true"])
        none_found = sum(1 for r in per_candidate if r["n_found"] == 0)

        print(f"Golden recommendation eval: {n} candidates, k={k}")
        print(f"  Precision@{k} (against known positives): {avg_precision:.4f}")
        print(f"  Recall@{k} (against known positives):    {avg_recall:.4f}")
        print(f"  Candidates where all 3 golden jobs were found:  {all_found}/{n}")
        print(f"  Candidates where NONE of the 3 were found:      {none_found}/{n}")
    finally:
        session.close()


if __name__ == "__main__":
    setup_logging("golden-recommendation-eval")
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only evaluate the first N golden candidates")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    args = parser.parse_args()
    main(limit=args.limit, k=args.k)
