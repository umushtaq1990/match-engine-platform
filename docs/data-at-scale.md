# Realistic, Scalable Synthetic Data Generation (Phase 20, design)

Status: v0.1 — **design only, not yet built**. Same discipline as every prior phase: written and
reviewed before any code exists.

## Two problems, one generator

1. **No signal for the knowledge graph to find.** Phase 19's root-cause investigation found the
   skill co-occurrence graph is *complete* (every one of the 64-choose-2 skill pairs has an edge)
   because `generate_synthetic_data.py` assigns each occupation's skill profile via
   `rng.sample(skills, k=rng.randint(4, 8))` — uniformly at random across all 64 skills, no
   category weighting. Real occupations cluster skills by domain; this dataset's don't.
2. **The generator can't reach LinkedIn-ish scale.** Today's generator builds every row as a
   Python ORM object in nested loops with one final `session.commit()` — fine at 2,000 candidates/
   1,000 jobs, not at the target scale of ~1,000,000 candidates / ~50,000 open jobs.

Both are fixed in the same pass, because fixing (1) changes the skill distribution at *any* scale,
and (1) is addressed before (2) is attempted — there's no point stress-testing a generator that
still produces data with no structure in it.

## Fix 1: category-coherent skill sampling, via skill "domains"

The 64 skills' existing `category` field (programming, data, trades, healthcare, etc. — ~19
categories) is too fine-grained to anchor a 4–8-skill profile on reliably: several categories have
only 1–3 skills (`frontend` has exactly one). Introduce a coarser grouping, **domain**, purely in
generator code (not a new database column) that groups related categories together:

| Domain | Categories grouped in | Approx. skill count |
|---|---|---|
| `software_engineering` | programming, frontend, backend, platform, cloud, ai, data, it | ~27 |
| `business_ops` | business, soft_skill, hr, office, finance | ~17 |
| `trades_logistics` | trades, logistics, engineering | ~10 |
| `people_services` | healthcare, education, creative, marketing | ~9 |

Each occupation is assigned **one anchor domain**; its skill profile draws ~70–80% of its skills
from that domain's pool and the remaining ~20–30% uniformly at random from anywhere — realistic
noise (real job postings do list tangential skills), not a perfectly pure category assignment.

**This cascades automatically, no other code changes needed**: `CandidateSkill` and `JobSkill`
sampling already derive their candidate pools from `OccupationSkill` (`occ_skill_ids` in the
existing code) rather than sampling the full taxonomy independently. Fixing occupation-level
sampling alone fixes candidate- and job-level skill coherence too.

**Validation stays non-circular**: Phase 19's clustering check compares discovered communities
against the existing `category` field, which is still never used directly as a sampling weight —
`domain` is the new code-level construct, built *from* categories, but the validation checks at
category granularity, a genuinely independent signal. Expect meaningfully higher modularity/purity
than Phase 19's baseline (0.045 / 0.16) — a real before/after to report, not assumed in advance.

## Fix 2: bulk-insert generation, configurable scale

- Replace per-row `session.add()` + one final commit with **batched `executemany`-style bulk
  inserts** (SQLAlchemy Core `Table.insert()` executed against lists of param dicts, chunked at
  ~10,000 rows per batch, committing each batch) — the standard pattern for this volume, not a
  novel one.
- Entity counts become CLI arguments / env-driven, not hardcoded module constants. **Two
  invocations, not one new default**:
  - **Default** (`python -m app.ingestion.generate_synthetic_data`): unchanged at 2,000/1,000/150/
    100/64 — every existing test, CI check, and documented baseline keeps working against the same
    scale it was measured against.
  - **At-scale** (`python -m app.ingestion.generate_synthetic_data --scale large`): ~1,000,000
    candidates, ~50,000 jobs, proportionally scaled occupations/courses (e.g. ~3,000/~2,000) — a
    real stress-test invocation, not the default anyone runs by accident.

## What changes at scale, and what honestly doesn't

| Layer | At 1M/50K | Why |
|---|---|---|
| Postgres (relational) | **Fully tested, for real** | Bulk insert is exactly what Postgres is built for; tens of millions of narrow rows is unremarkable at its scale |
| Neo4j (graph sync + GDS) | **Fully tested, for real** | `sync_graph.py`'s batched `MERGE` pattern and GDS's native algorithms are designed for graphs far larger than this |
| Matching engine (`rank.py`) | **Fully tested, for real** | Pure Python/SQL, no LLM calls — Phase 9's N+1 fix already makes this a fixed small number of queries regardless of row count |
| Embeddings (Phase 17) | **Sampled, not full-population** | Real local LLM embedding calls (~1–2s each via Ollama) for 1M candidates is weeks of compute, not a realistic local demo — see below |
| Extraction (Phase 16) | **Sampled, not full-population** | Same constraint — LLM calls don't scale locally the way bulk SQL inserts do |

**Honest architecture note, not a cop-out**: this is itself the real-world answer to "how would this
actually scale" — a production deployment at this volume would use a managed/batched embedding
API or a GPU cluster for the embedding workload, not a single local Ollama instance. Demonstrating
*where* the architecture needs that substitution is a legitimate finding, not a gap to hide.

**pgvector index load-testing, honestly labeled**: to test HNSW index behavior at real volume
without claiming fake content is real, the embedding table is populated two ways — a
representative sample (a few thousand candidates/jobs) gets **real** computed embeddings for
content-quality evaluation (unchanged from Phase 17), while the remaining rows get **random filler
vectors**, explicitly labeled as such, purely to test index build/query performance at scale — a
standard, legitimate ANN-benchmarking technique, never presented as real semantic content.

## Re-verification plan — every downstream baseline gets refreshed honestly

Because Fix 1 changes the *default*-scale skill distribution, every measured number that depends
on it is now stale the moment this ships, and gets re-run, not left outdated:

- Phase 4/9 evaluation (`docs/evaluation-strategy.md`) — Precision@K/NDCG@K/MRR re-measured.
- Phase 16 extraction eval (`docs/skill-extraction.md`) — golden dataset re-generated from the new
  distribution, baseline re-measured.
- Phase 17 embedding eval (`docs/embedding-matching.md`) — rule-based vs. blended vs.
  embeddings-only re-measured.
- Phase 19 clustering (`docs/knowledge-graph.md`) — the whole point of this phase; expect (not
  assume) a real improvement, reported honestly either way.

## Known limitations (v1, by design)

- Domain-to-category grouping is a judgment call (four domains chosen to balance pool size against
  realism), not validated against any real occupational taxonomy (e.g. O*NET) — documented as a
  modeling choice, not a validated external fact.
- At-scale embeddings/extraction are sampled, not full-population — see table above.
- Random filler vectors for pgvector volume-testing are explicitly not real content — any analysis
  of embedding *quality* must stay scoped to the real sample, never the filler rows.
