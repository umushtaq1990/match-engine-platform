# Realistic, Scalable Synthetic Data Generation

Status: v1 — **implemented, verified at both the default and the 1M/50K target scale**, with a
real downstream bug found and fixed along the way. This document was written and reviewed before
any code existed, the same discipline followed throughout this project.

## Purpose & Scope

The synthetic data generator (`app/ingestion/generate_synthetic_data.py`) needed to solve two
problems at once:

1. **No signal for the knowledge graph to find.** A root-cause investigation found the skill
   co-occurrence graph is *complete* (every one of the 64-choose-2 skill pairs has an edge) because
   the generator assigns each occupation's skill profile via `rng.sample(skills, k=rng.randint(4,
   8))` — uniformly at random across all 64 skills, no category weighting. Real occupations cluster
   skills by domain; this dataset's didn't.
2. **The generator can't reach LinkedIn-ish scale.** The original generator built every row as a
   Python ORM object in nested loops with one final `session.commit()` — fine at 2,000 candidates/
   1,000 jobs, not at the target scale of ~1,000,000 candidates / ~50,000 open jobs.

Both are fixed in the same pass, because fixing (1) changes the skill distribution at *any* scale,
and (1) is addressed before (2) is attempted — there's no point stress-testing a generator that
still produces data with no structure in it.

Scope boundary: embeddings and extraction remain sampled, not full-population, at the 1M/50K scale
(see Known Limitations) — this document covers the relational/graph data path, which is fully
tested at that scale.

## Architecture & Design

**Fix 1: category-coherent skill sampling, via skill "domains".** The 64 skills' existing
`category` field (programming, data, trades, healthcare, etc. — ~19 categories) is too fine-grained
to anchor a 4–8-skill profile on reliably: several categories have only 1–3 skills (`frontend` has
exactly one). The fix introduces a coarser grouping, **domain**, purely in generator code (not a
new database column) that groups related categories together:

| Domain | Categories grouped in | Approx. skill count |
|---|---|---|
| `software_engineering` | programming, frontend, backend, platform, cloud, ai, data, it | ~27 |
| `business_ops` | business, soft_skill, hr, office, finance | ~17 |
| `trades_logistics` | trades, logistics, engineering | ~10 |
| `people_services` | healthcare, education, creative, marketing | ~9 |

Each occupation is assigned **one anchor domain**; its skill profile draws ~70–80% of its skills
from that domain's pool and the remaining ~20–30% uniformly at random from anywhere — realistic
noise (real job postings do list tangential skills), not a perfectly pure category assignment.

This cascades automatically, no other code changes needed: `CandidateSkill` and `JobSkill` sampling
already derive their candidate pools from `OccupationSkill` (`occ_skill_ids` in the existing code)
rather than sampling the full taxonomy independently. Fixing occupation-level sampling alone fixes
candidate- and job-level skill coherence too.

Validation stays non-circular: the knowledge-graph clustering check (`docs/knowledge-graph.md`)
compares discovered communities against the existing `category` field, which is still never used
directly as a sampling weight — `domain` is the new code-level construct, built *from* categories,
but the validation checks at category granularity, a genuinely independent signal.

**Fix 2: bulk-insert generation, configurable scale.**
- Replaced per-row `session.add()` + one final commit with **batched `executemany`-style bulk
  inserts** (SQLAlchemy Core `Table.insert()` executed against lists of param dicts, chunked at
  ~10,000 rows per batch, committing each batch) — the standard pattern for this volume, not a
  novel one.
- Entity counts became CLI arguments / env-driven, not hardcoded module constants. Two invocations,
  not one new default:
  - **Default** (`python -m app.ingestion.generate_synthetic_data`): unchanged at 2,000/1,000/150/
    100/64 — every existing test, CI check, and documented baseline keeps working against the same
    scale it was measured against.
  - **At-scale** (`python -m app.ingestion.generate_synthetic_data --scale large`): ~1,000,000
    candidates, ~50,000 jobs, proportionally scaled occupations/courses (e.g. ~3,000/~2,000) — a
    real stress-test invocation, not the default anyone runs by accident.

**pgvector index load-testing, honestly labeled**: to test HNSW index behavior at real volume
without claiming fake content is real, the embedding table is populated two ways — a representative
sample (a few thousand candidates/jobs) gets **real** computed embeddings for content-quality
evaluation (unchanged from the original embedding design), while the remaining rows get **random
filler vectors**, explicitly labeled as such, purely to test index build/query performance at
scale — a standard, legitimate ANN-benchmarking technique, never presented as real semantic
content.

## Implementation

What changes at scale, and what honestly doesn't:

| Layer | At 1M/50K | Why |
|---|---|---|
| Postgres (relational) | **Fully tested, for real — 11m40s to generate ~7.4M rows** | Bulk insert is exactly what Postgres is built for; tens of millions of narrow rows is unremarkable at its scale |
| Neo4j (graph sync + GDS) | **Fully tested, for real — 15m50s to sync ~1.06M nodes and ~6.4M edges** | `sync_graph.py`'s batched `MERGE` pattern and GDS's native algorithms are designed for graphs far larger than this |
| Matching engine (`rank.py`) | **Fully tested, for real** | Pure Python/SQL, no LLM calls — an earlier N+1 fix already makes this a fixed small number of queries regardless of row count |
| Embeddings | **Sampled, not full-population** | Real local LLM embedding calls (~1–2s each via Ollama) for 1M candidates is weeks of compute, not a realistic local demo |
| Extraction | **Sampled, not full-population** | Same constraint — LLM calls don't scale locally the way bulk SQL inserts do |

**Honest architecture note, not a cop-out**: this is itself the real-world answer to "how would this
actually scale" — a production deployment at this volume would use a managed/batched embedding API
or a GPU cluster for the embedding workload, not a single local Ollama instance. Demonstrating
*where* the architecture needs that substitution is a legitimate finding, not a gap to hide.

**Re-verification discipline**: because the domain-anchoring fix changes the *default*-scale skill
distribution, every measured number that depends on it goes stale the moment this ships, and gets
re-run, not left outdated — matching evaluation (`docs/evaluation-strategy.md`), extraction
evaluation (`docs/skill-extraction.md`), embedding evaluation (`docs/embedding-matching.md`), and
knowledge-graph clustering (`docs/knowledge-graph.md`) were all re-measured as part of this change.

## Verification & Results

**Generation performance, measured, not estimated**:

| Invocation | Rows produced | Wall time |
|---|---|---|
| `--scale small` (default, unchanged counts) | ~22,000 total rows | ~7s |
| `--scale large` (1M candidates / 50K jobs) | ~1,075,000 base rows + **5,833,536** `candidate_skills` + 301,531 `job_skills` + 200,607 assessments ≈ **7.4M rows total** | **11m40s** |

**Domain-anchored sampling fixed the original uniform-random finding, measured before/after**:

| | Before (uniform-random) | After (domain-anchored) |
|---|---|---|
| Mean category purity | 0.1585 | **0.3222** |
| Discovered communities vs. designed domains | No correspondence | **Perfect 1:1 recovery** — all 4 communities map exactly onto `software_engineering`/`business_ops`/`trades_logistics`/`people_services` with zero cross-contamination |

The co-occurrence graph is still topologically complete (2,016 edges — every skill pair still
co-occurs at least once across 2,000+ candidates/jobs/occupations), but the **weight distribution**
is now sharply differentiated (min 42, max 335, vs. the old min 3, max 83) — strong within-domain
co-occurrence, weak cross-domain. That's sufficient signal for weighted Louvain to recover clean
communities even without graph sparsity — a more precise, more honest finding than "fixed it" would
be: the graph's *topology* didn't change, its *weights* did, and that was enough.

**A real downstream bug found by the 50K-job stress test, not by code review**: `rank_jobs_for_
candidate` (rule-based) took **3.5s** for one candidate against 50,000 jobs — consistent with the
system's measured ~0.06s/job rate at 50x the job count. But `rank_jobs_for_candidate_semantic`
(the embeddings path, supposedly immune to job-count scaling via ANN retrieval) took an
unexpectedly slow **1.637s** even with only 300 jobs embedded. Investigated, not assumed: traced to
`app/embeddings/retrieval.py` calling `load_job_profile` and three separate `_get_vector` queries
**per job in the stage-2 loop** — up to 5 queries × 200 jobs = 1,000 queries per request, an N+1
pattern reintroduced here after being fixed once elsewhere. Fixed with `load_job_profiles_by_ids`
(`app/matching/profiles.py`) and `_get_vectors_batch` (`app/embeddings/retrieval.py`) — both
batch-load once per request instead of once per job. **Re-measured: 1.637s → ~0.11–0.21s, a
~10-15x improvement**, confirmed via the live `/job-matches/semantic` endpoint too (1.2s round-trip
over HTTP, including network overhead).

**The architectural point this was all for, confirmed live at real scale**: against the full
50,000-job dataset, the rule-based endpoint took **10.7s** per request; the (now-fixed) semantic
endpoint took **1.2s** — not because embeddings are inherently faster per comparison, but because
ANN retrieval narrows the candidate set before scoring, while the rule-based path scores every job
on every request. This is the concrete, measured case for the two-stage retrieve-then-score
architecture (`docs/embedding-matching.md`), not a theoretical argument.

**Every downstream baseline re-verified, not left stale**:

| Harness | Before (uniform-random data) | After (domain-anchored data) |
|---|---|---|
| Matching evaluation (`docs/evaluation-strategy.md`) | P@10 0.4878, NDCG 0.7461 | P@10 0.4964, NDCG 0.7352 — stable, as expected (occupation-alignment ground truth is unchanged) |
| Extraction evaluation (`docs/skill-extraction.md`) | F1 0.504–0.664 | **F1 0.6766** — consistent with the earlier `qwen2.5:1.5b` improvement |
| Embedding evaluation (`docs/embedding-matching.md`) | embeddings-only *underperformed* rule-based (NDCG 0.776 vs 0.849) | **embeddings-only now *beats* rule-based** (NDCG 0.798 vs 0.645) — a direct, measured consequence of skills finally carrying real domain signal; blended is better still (NDCG 0.878) |

The embedding-matching reversal is the most interesting result from this work: embeddings-based
matching was never weaker *in principle* — it was only ever as good as the skill data it had to
work with.

## Known Limitations

- Domain-to-category grouping is a judgment call (four domains chosen to balance pool size against
  realism), not validated against any real occupational taxonomy (e.g. O*NET) — documented as a
  modeling choice, not a validated external fact.
- At-scale embeddings/extraction are sampled, not full-population — see Implementation table above.
- Random filler vectors for pgvector volume-testing are explicitly not real content — any analysis
  of embedding *quality* must stay scoped to the real sample, never the filler rows.

## Related Documents

- `docs/evaluation-strategy.md` — matching quality evaluation, re-verified against this data
- `docs/skill-extraction.md` — extraction evaluation, re-verified against this data
- `docs/embedding-matching.md` — the two-stage retrieve-then-score architecture and the reversed
  embeddings-vs-rule-based finding
- `docs/knowledge-graph.md` — the clustering this data-generation fix was built to enable
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
