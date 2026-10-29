# Evaluation Strategy (Phase 4)

Status: v0.1, implemented and run once — baseline established, not yet used to compare
alternatives (that's the next step, not done here).

## Why this exists before any ML/embedding work

Per the project's own rule: no ML approach (embeddings, LLM-based extraction) should be adopted
without a way to measure whether it's actually better than the deterministic baseline. This phase
builds that measurement capability first — a direct prerequisite for the deferred Use Case C work
(see `docs/assumptions-register.md` A5b).

## Avoiding circular evaluation

The central design risk in any synthetic evaluation: if "relevant" is defined using the same
formula being tested, the evaluation proves nothing — the system would score perfectly by
construction. `app/evaluation/ground_truth.py` deliberately avoids this: ground truth is defined
using **only occupation alignment** (`Candidate.primary_occupation_id == Job.occupation_id`),
while the scoring formula being evaluated (`app/matching/scoring.py`) blends skill, location,
salary, *and* occupation. Measuring the full blended ranking against an occupation-only ground
truth is meaningful precisely because they're not the same computation.

This is still a **synthetic, coarse** definition — a real recruiter's notion of "relevant"
accounts for skill gaps, which this ground truth deliberately ignores (that's what
`skill_gap.py` analyzes separately). Per the brief: this does not prove real-world effectiveness,
only internal consistency of the ranking logic against a documented, defensible synthetic label.

## Metrics implemented

Standard binary-relevance IR metrics (`app/evaluation/metrics.py`, pure functions, 14 unit
tests): Precision@K, Recall@K, NDCG@K, Mean Reciprocal Rank (MRR, averaged across candidates).

## Sample size — measured, not guessed

Before picking a sample size, actual per-candidate ranking latency was measured: **~1.95s per
candidate** (5-candidate timing test), consistent with the already-documented N+1 query
limitation in `app/matching/profiles.py`. Evaluating the full 1,899 eligible candidates would take
over an hour — not reasonable for this phase. Evaluation runs against a **deterministic sample of
50** (lowest 50 candidate IDs with usable ground truth), not the full population. Full-population
evaluation is a Phase 9 follow-up, once the N+1 query cost is addressed — not attempted now.

## Results storage

Same two-level pattern as `data_quality_runs`/`data_quality_check_results`:
`evaluation_runs` (one row per run: k, sample size, excluded count, config label, averaged
metrics) and `evaluation_candidate_results` (per-candidate detail). `config_label` captures the
exact scoring weights used, so future weight changes can be compared against this run rather than
guessed about.

## Baseline result (run #1, measured, 2026-10-29)

| Metric | Value |
|---|---|
| k | 10 |
| Sample size | 50 (of 1,899 eligible; 101 candidates excluded — no `primary_occupation_id`) |
| Config | `skill=0.55, location=0.15, salary=0.15, occupation=0.15` |
| Precision@10 | 0.47 |
| Recall@10 | 0.7458 |
| NDCG@10 | 0.7283 |
| MRR | 0.8029 |

**Reading these honestly**: Recall@10 of 0.75 and MRR of 0.80 indicate the ranking generally
surfaces occupation-aligned jobs near the top — expected, since occupation is one of the four
scored components. Precision@10 of 0.47 means on average fewer than half of the top-10
recommended jobs are occupation-aligned; the rest are jobs the system ranked highly on
skill/location/salary despite a different occupation. Whether that's desirable (surfacing
adjacent/transferable opportunities) or a weakness (diluting relevance) is exactly the kind of
question this harness is built to let us investigate by comparing alternative weight
configurations — not resolved here, since no comparison has been run yet.

## Known limitations

- Sample of 50, not the full population (see above).
- Ground truth is occupation-only — ignores skill gaps and salary/location fit entirely. A
  candidate could be marked "relevant" for a job they're a poor fit for on every other dimension.
- No test coverage yet for `ground_truth.py`/`run_evaluation.py` themselves beyond manual
  verification against the real database (consistent with how `data_quality.py` and `rank.py`
  were verified — pure logic gets unit tests, DB-touching orchestration gets verified by
  inspecting real output, not a DB test fixture).
- This is run #1 — a baseline, not yet used to compare alternative scoring weights. That
  comparison is the natural next step, not done in this phase.
