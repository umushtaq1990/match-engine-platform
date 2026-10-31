# Evaluation Strategy

Status: v0.2, full-population baseline established (1,899 candidates) — not yet used to compare
alternatives (deferred by explicit user instruction, see `docs/progress.md`).

## Purpose & Scope

Per the project's own rule: no ML approach (embeddings, LLM-based extraction) should be adopted
without a way to measure whether it's actually better than the deterministic baseline. This
subsystem builds that measurement capability first — a direct prerequisite for the deferred Use
Case C work (see `docs/assumptions-register.md` A5b) and for the embedding-matching comparison
(`docs/embedding-matching.md`).

Out of scope: comparing alternative scoring-weight configurations against each other — the harness
supports it, but no comparison run has happened yet (explicitly deferred, user's call, until other
architecture work finished first).

## Architecture & Design

**Avoiding circular evaluation.** The central design risk in any synthetic evaluation: if
"relevant" is defined using the same formula being tested, the evaluation proves nothing — the
system would score perfectly by construction. `app/evaluation/ground_truth.py` deliberately avoids
this: ground truth is defined using **only occupation alignment**
(`Candidate.primary_occupation_id == Job.occupation_id`), while the scoring formula being evaluated
(`app/matching/scoring.py`) blends skill, location, salary, *and* occupation. Measuring the full
blended ranking against an occupation-only ground truth is meaningful precisely because they're not
the same computation.

This is still a **synthetic, coarse** definition — a real recruiter's notion of "relevant" accounts
for skill gaps, which this ground truth deliberately ignores (that's what `skill_gap.py` analyzes
separately). Per the brief: this does not prove real-world effectiveness, only internal consistency
of the ranking logic against a documented, defensible synthetic label.

**Metrics implemented**: standard binary-relevance IR metrics (`app/evaluation/metrics.py`, pure
functions, 14 unit tests): Precision@K, Recall@K, NDCG@K, Mean Reciprocal Rank (MRR, averaged across
candidates).

**Results storage**, split by purpose, not duplicated across two systems tracking the same thing:
- **MLflow** (`docker/Dockerfile.mlflow`, always-on service at `http://localhost:5000`, backend
  store: a dedicated `mlflow` Postgres database on the same instance) tracks **run-level** params
  (scoring weights, k, sample size) and aggregate metrics — a browsable, comparable experiment
  history across scoring-weight configs, exactly the "show what was tried" capability this needed.
- **Postgres** (`evaluation_runs`/`evaluation_candidate_results`, same two-level pattern as
  `data_quality_runs`) keeps the **per-candidate detail** rows — for drill-down analysis and a
  future dashboard, which queries Postgres more naturally than MLflow's own store.

MLflow logging is best-effort: `run_evaluation.py` always persists to Postgres first; if the MLflow
server is unreachable, the run still completes and only the MLflow log is skipped (with a warning),
not treated as a fatal error.

## Implementation

**Sample size — measured, not guessed.** The first measurement: **~1.95s per candidate**
(5-candidate timing test), consistent with an undiagnosed N+1 query limitation in
`app/matching/profiles.py` at the time. Evaluating the full 1,899 eligible candidates would have
taken over an hour, so evaluation initially ran against a sample of 50 instead.

Once the N+1 limitation was fixed (batch-loading instead of one query per row — see
`docs/matching-engine.md`), per-candidate latency dropped to ~0.06s. Full-population evaluation (all
1,899 eligible candidates) now completes in ~2m25s and is the default (`DEFAULT_SAMPLE_SIZE` in
`run_evaluation.py`); the original 50-candidate cap is no longer necessary.

## Verification & Results

**A real bug caught and fixed while wiring this up.** First run against the live MLflow server
crashed with `UnicodeEncodeError` — MLflow prints a confirmation emoji (`🏃 View run at: ...`) to
stdout after a run finishes, which this Windows console (cp1252) can't encode. The crash happened
*during* `mlflow.end_run()`, before the run was marked `FINISHED` in MLflow's backend, leaving a
stuck `RUNNING` run behind even though the params/metrics had already been logged successfully
(verified via MLflow's REST API before concluding this). Fixed using MLflow's own supported
suppression flag (`MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT`), not a workaround; the stuck run was
deleted via the MLflow API once the fix was confirmed on a clean re-run.

**Baseline results across three measurement rounds**:

| Metric | Round 1 (sample of 50) | Round 2 (full population, 1,899) | Round 3 (full population, domain-anchored data) |
|---|---|---|---|
| k | 10 | 10 | 10 |
| Config | `skill=0.55, location=0.15, salary=0.15, occupation=0.15` | same | same |
| Precision@10 | 0.47 | 0.4878 | 0.4964 |
| Recall@10 | 0.7458 | 0.7625 | 0.7587 |
| NDCG@10 | 0.7283 | 0.7461 | 0.7352 |
| MRR | 0.8029 | 0.8032 | 0.7975 |

The full-population numbers (Round 2) are close to the 50-candidate sample (Round 1 — largest gap:
Precision@10, 0.47 vs. 0.4878) — a useful confirmation that the original sample, while small, wasn't
misleading. The full run is still the one future weight-comparison work should use, now that it's
cheap enough to be the default rather than a documented compromise.

**Round 3 update**: the synthetic generator later changed (`docs/data-at-scale.md` — occupation
skill profiles are now anchored to a coherent "domain" instead of sampled uniformly at random, to
give the knowledge graph real clustering signal). Re-ran this exact evaluation against the new data:
ranking quality stayed essentially stable (differences within normal run-to-run noise), which is the
expected, reassuring result — the occupation-alignment ground truth itself didn't change, only
*which* skills co-occur within an occupation did. The real quality shift from this data change shows
up in the embedding-based evaluation instead, not here — see `docs/embedding-matching.md`.

**Reading these honestly**: Recall@10 of ~0.76 and MRR of ~0.80 indicate the ranking generally
surfaces occupation-aligned jobs near the top — expected, since occupation is one of the four scored
components. Precision@10 of ~0.49 means on average about half of the top-10 recommended jobs are
occupation-aligned; the rest are jobs the system ranked highly on skill/location/salary despite a
different occupation. Whether that's desirable (surfacing adjacent/transferable opportunities) or a
weakness (diluting relevance) is exactly the kind of question this harness is built to let us
investigate by comparing alternative weight configurations — still not resolved here, since no
comparison has been run yet (see Known Limitations).

## Known Limitations

- Ground truth is occupation-only — ignores skill gaps and salary/location fit entirely. A candidate
  could be marked "relevant" for a job they're a poor fit for on every other dimension.
- No test coverage yet for `ground_truth.py`/`run_evaluation.py` themselves beyond manual
  verification against the real database (consistent with how `data_quality.py` and `rank.py` were
  verified — pure logic gets unit tests, DB-touching orchestration gets verified by inspecting real
  output, not a DB test fixture).
- No scoring-weight comparison has been run yet — explicitly deferred (user's call) until other
  architecture work finished first. The harness (and now full-population evaluation) is ready for it
  whenever that happens.

## Related Documents

- `docs/matching-engine.md` — the scoring logic this evaluates, and the N+1 fix that unlocked
  full-population evaluation
- `docs/embedding-matching.md` — the three-way rule-based/blended/embeddings-only comparison built
  on top of this same metrics harness
- `docs/data-at-scale.md` — the domain-anchored data change behind Round 3
- `docs/assumptions-register.md` — assumption A5/A5b, which this evaluation work resolves
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
