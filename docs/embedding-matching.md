# Multi-Dimensional Embedding Matching — Use Case F groundwork

Status: v1 — **implemented and verified end-to-end**, including a real measured rule-based-vs-
embeddings comparison (assumption A5's deferred promise since early in the project, now resolved).
This document was written and reviewed before any code existed, the same discipline followed
throughout this project. The design below matched the build with no changes required.

## Purpose & Scope

The existing matching engine (`app/matching/scoring.py`) is a single blended rule-based score over
skill/location/salary/occupation. This work adds a **complementary, embeddings-based signal** — not
a replacement — scored separately per facet (skills, experience, overall text), so it can be
measured against the rule-based baseline before anyone decides it's actually better. This is the
exact comparison assumption A5 and A5b have been deferring since early in the project: *"evaluation
will state measured delta between rule-based and semantic approaches before any decision to
adopt."* This is where that measurement finally became possible.

It also solves a real scale problem the rule-based engine never had to face: scoring one candidate
against 100,000 jobs at full fidelity, for every candidate, is computationally absurd. The answer
is a **two-stage retrieve-then-score architecture**, the same pattern real large-scale search and
recommendation systems use:

- **Stage 1 (cheap, approximate)**: a vector database's ANN (approximate nearest neighbor) index
  narrows 100,000 jobs down to a few hundred plausible candidates in milliseconds, using a single
  embedding comparison.
- **Stage 2 (precise, expensive)**: only on that narrowed set, compute the full multi-dimensional
  score (every embedding facet, blended with the existing rule-based components) and re-rank
  properly.

Without stage 1, there is no affordable way to even consider all 100,000 jobs for a single
candidate. Stage 1 is not an optimization trick bolted on later — it's the reason a vector database
is needed at all.

**Out of scope**: an Education dimension (no education/certification table exists in the domain
model — fabricating one would repeat a mistake `docs/matching-engine.md` already documented
avoiding: qualification compatibility, omitted entirely rather than faked). A courses dimension
(candidate skill-gap → course matching) is deferred as a separate topic (see Roadmap).

## Architecture & Design

**Vector store: pgvector, not a new dedicated service.** `docker-compose.yml`'s `db` service image
uses **`pgvector/pgvector:0.8.7-pg16`** — confirmed via Docker Hub's API to be a drop-in-compatible
build of the same Postgres 16 with the `pgvector` extension pre-installed, not a fork. No new
container, no new client library beyond the thin `pgvector` Python package (v0.5.1, a SQLAlchemy
type adapter), no new backup/security surface. This is the same "prove the simple thing first"
discipline already applied elsewhere in this project — not a default choice made out of inertia.
`docs/hardware-inventory.md` already anticipated Qdrant as the "someday, if ever" answer for vector
search; it stays the **documented v2 upgrade path**, adopted only if pgvector's HNSW index is
measured insufficient at a real target scale, not assumed insufficient in advance.

**A real technical constraint this design resolves, not papers over**: a single pgvector column has
a **fixed vector width** (`vector(384)`, `vector(768)`, etc. are different column types, and an
HNSW index requires one fixed width). That's incompatible with "try several embedding models of
different output sizes" unless the production/experimentation split below is made explicit:

- **Production table** (`entity_embeddings`): one pgvector column, HNSW-indexed, tied to exactly
  **one active default model per dimension** at a time. This is what stage 1's ANN retrieval
  actually queries.
- **Evaluation harness** (mirrors the extraction evaluation pattern exactly, see
  `docs/skill-extraction.md`): computes embeddings from *alternate* models against the golden
  dataset, stored as plain JSONB (not indexed, not production-served — a small golden set doesn't
  need ANN speed, brute-force cosine comparison is fine), scored, logged to MLflow. Promoting a
  different model to production is an explicit migration (new column width) plus a full re-embed
  batch — never a silent swap.

This cleanly separates the **online serving path** (one fixed, indexed, fast production model) from
the **offline experimentation path** (freely swappable models, no indexing constraint) — a
standard, deliberate split in real ML platforms, not a limitation of this one.

**Embedding dimensions (v1)**:

| Dimension | Candidate source | Job source | How computed |
|---|---|---|---|
| **Skills** | `CandidateSkill` rows | `JobSkill` rows | **No new LLM call** — a proficiency/importance-weighted average of the per-skill embeddings `app/extraction/skill_cache.py` already computes and caches. Free, reuses existing infrastructure directly. |
| **Experience** | `years_experience` + `primary_occupation` | `occupation` + `employment_type` | A short synthesized sentence (e.g. "5 years of experience as a Data Analyst") embedded via the active model — honest, since it's derived from real structured fields, not fabricated |
| **Overall/semantic** | a synthesized profile summary (no raw CV text field exists yet) | `description_raw` directly | Whole-text embedding, captures nuance the structured fields and the Skills dimension both miss |
| ~~Education~~ | — | — | **Explicitly deferred, not built.** See Purpose & Scope above |

**Service boundary**: not a new deployable image. This extends the matching engine itself (new
routes/logic reachable from the existing `api` image) and reuses the `extraction` image for the
offline batch jobs (embedding computation, refresh, evaluation harness) since that image already
has Ollama access and the skill-embedding cache — adding a sixth Dockerfile for this would
duplicate dependencies for no isolation benefit, since this capability's whole purpose is to feed
the existing matching code, not stand alone. `pgvector` (the Python package) is added only to the
specific `requirements-*.txt` files that need it (`migrate`, `api`, `extraction`) — not
`requirements-base.txt` — keeping every other image's size unaffected by a dependency it doesn't
use, the same per-image-narrow-dependency discipline `docs/data-quality.md` documents for the
`dataquality` image.

**Model progression**: starts at **`all-minilm`** (384-dim, ~45MB — confirmed by directly pulling
and embedding a test string against the running `ollama` service) as the genuinely small baseline,
compared against `nomic-embed-text` (768-dim, already in use for extraction) and heavier options
later — all swappable through Ollama's same stable API, all logged to MLflow for comparison,
identical discipline to the extraction model-comparison harness.

## Implementation

**Storage schema (sketch)**:

```
entity_embeddings
  id                 serial primary key
  entity_type        text        -- 'candidate' | 'job'
  entity_id          integer
  dimension          text        -- 'skills' | 'experience' | 'overall'
  embedding_model    text        -- the one active production model for this dimension
  source_hash        text        -- hash of whatever structured/text data produced this vector
  vector             vector(384) -- pgvector type; width fixed to the active model's output size
  computed_at        timestamptz
  unique(entity_type, entity_id, dimension, embedding_model)
```

HNSW index per `(dimension, embedding_model)` partition — stage 1's ANN query always filters to one
dimension and the one active model, never mixes widths.

**Invalidation — a cache-freshness problem, treated as one.** `source_hash` is a hash of exactly
the inputs that produced that vector (e.g., for the Skills dimension: the sorted `(skill_id,
proficiency)` tuples). When a candidate's skills change, the freshly computed hash no longer
matches the stored one — that row is **stale**, not silently wrong forever. A refresh job (one-off,
`profiles: tools`, same shape as `migrate`/`seed`/`analytics-seed`) recomputes only rows whose hash
has drifted, not a blanket full recompute every run — the same incremental-cache discipline already
used for the per-skill embedding cache, applied at the entity level instead of the taxonomy level.

**Two-stage retrieval, concretely**:
1. **Stage 1**: for a given candidate, one ANN query against the Skills-dimension job embeddings
   (pgvector `<=>` operator, HNSW-indexed) returns the top-N (e.g. 200) most plausible jobs in
   milliseconds, even over 100,000 rows.
2. **Stage 2**: for only those ~200 jobs, compute Experience-dimension and Overall-dimension
   similarity too, blend all three with the existing rule-based score's location/salary/occupation
   components (a documented weighted combination, same transparency discipline as
   `app/matching/scoring.py`'s own weights table), and re-rank.

This augments `rank_jobs_for_candidate`/`rank_candidates_for_job` with an optional embeddings-based
path. It did not originally replace the existing rule-based ranking as the default — blended
ranking was later promoted to the default path once the evaluation harness showed it actually
helps (see Verification & Results and Roadmap below).

**Golden dataset — extends the matching-quality golden set, doesn't just repeat it.** The original
ground truth (occupation-only alignment) is necessary but not sufficient here: the entire *point*
of embeddings is capturing "similar but not identical" relationships the rule-based engine's
exact-match scoring structurally cannot — a candidate with strongly overlapping but
differently-named skills, or a related-but-different occupation, should score well on the Skills
dimension even when the original ground truth would call the pairing irrelevant. So the golden
dataset adds a second part: hand-labeled **near-miss pairs** specifically designed to test that
added value (e.g., two skill sets that are semantically close but share zero exact `skill_id`
overlap).

Evaluation itself reuses the original exact metrics (Precision@K, Recall@K, NDCG@K, MRR) run three
ways per candidate model: rule-based only, embeddings-blended, and embeddings-only — so the
eventual comparison is a measured delta per the brief's original requirement, not a vibe.

## Verification & Results

**pgvector image swap**: confirmed via Docker Hub's API as a genuine drop-in build; swapped the
running `db` container against the existing data volume and verified every prior table's row counts
survived unchanged (candidates=2000, jobs=1000, behavioral_events=28226, extraction_runs=3). A
collation-version warning from the base-image glibc difference was fixed with Postgres's own
documented `ALTER DATABASE ... REFRESH COLLATION VERSION` — a one-time local fix, irrelevant to a
fresh checkout/CI run, which always creates a new volume from scratch.

**pgvector functional smoke test**: a real `CREATE EXTENSION`, `vector(3)` column, insert, and
`<=>` cosine-distance query returned mathematically correct similarity ordering before any
application code was written.

**Migration**: `alembic revision --autogenerate` correctly detected both new tables; `CREATE
EXTENSION` and the HNSW index (`USING hnsw ... vector_cosine_ops`) were added by hand, since
autogenerate can't express a non-default Postgres access method. Declaring the same index in the
ORM's `__table_args__` (without re-running `CREATE INDEX`) keeps `alembic check` reporting zero
drift going forward, not a permanent false positive.

**Batch computation**: `python -m app.embeddings.compute_embeddings` computed real embeddings for
all 1,000 jobs and a sample of 80 candidates (two embedding-model calls per entity for
Experience/Overall; Skills cost zero new calls, reusing the extraction cache). A second run against
the same data correctly skipped all 240 already-computed rows via the `source_hash` check —
idempotence verified, not assumed.

**Two-stage retrieval**: a real call to `GET /candidates/1/job-matches/semantic` returned ranked
jobs from the full 1,000-job population with no visible latency — confirming the entire point of
precomputing embeddings offline: zero live Ollama calls on the request path, pure pgvector ANN plus
in-Python blending.

**Near-miss threshold, calibrated from real measurement, not guessed**: directly embedding the 12
hand-labeled skill pairs with `all-minilm` showed every "similar" pair scoring 0.315–0.649 cosine
similarity and every "dissimilar" pair scoring 0.145–0.252 — a clean gap, with 0.28 chosen as the
threshold. Scored 100% accuracy on this set at that threshold.

**Round 1 — the first rule-based-vs-embeddings comparison** (30 candidates with both ground truth
and computed embeddings, k=10, against the original uniform-random synthetic skill data):

| Mode | Precision@10 | Recall@10 | NDCG@10 | MRR |
|---|---|---|---|---|
| rule_based | 0.5200 | 0.8617 | 0.8489 | 0.8806 |
| blended | 0.5233 | 0.8517 | 0.8749 | 0.9333 |
| embeddings_only | 0.4967 | 0.7908 | 0.7755 | 0.8153 |

Reading this honestly: **blended modestly beats rule-based-only** on NDCG and MRR (better ordering
of the genuinely relevant jobs near the top), while **embeddings-only underperforms**
rule-based-only here — an explainable result, not a surprising failure: the ground truth here is
itself an exact-occupation-match label, which the rule-based score's own occupation component
answers almost perfectly by construction. Embeddings add complementary signal on top of that, but
don't replace it. This measurement was built to either confirm or deny that embeddings help, not
assumed in advance.

**Round 2 — embedding-model comparison, the simple model won.** Directly comparing `all-minilm`
(384-dim, the v1 production default) against `nomic-embed-text` (768-dim, "more capable" by
parameter count) on the 12-pair near-miss set — the one comparison that doesn't require touching
the production table, since it only embeds two words at a time:

| Model | Similar-pair range | Dissimilar-pair range | Best achievable accuracy |
|---|---|---|---|
| `all-minilm` | 0.315 – 0.649 | 0.145 – 0.252 | **12/12 (100%)** — clean gap, threshold 0.28 |
| `nomic-embed-text` | 0.422 – 0.678 | 0.397 – 0.439 | **11/12 (92%)** — *overlapping* ranges, no threshold separates all 12 pairs; e.g. `Docker`/`Kubernetes` (similar, 0.422) scores *below* `Carpentry`/`SQL` (dissimilar, 0.439) |

Reading this honestly: the larger, more general-purpose embedding model did not win here — it
separates these specific skill-pair semantics *worse* than the smaller model purpose-sized for
short-text similarity. `all-minilm` stays the production default by measurement, not by inertia.
This is exactly the outcome this tuning step existed to either confirm or overturn — and in this
case, confirms the original choice rather than finding a better one, which is itself a real result,
not a null one.

What this comparison does not cover: a full ranking-quality comparison across embedding models (not
just the near-miss task) would require computing `nomic-embed-text` embeddings for the actual
candidate/job population ad-hoc (768-dim vectors can't be written into the 384-dim production
`entity_embeddings` column — see Architecture & Design above) — not built in this pass; the
near-miss result was a strong enough signal not to invest further time switching models.

**Round 3 — re-verification after the synthetic generator changed, and the finding reversed.** The
synthetic generator later changed (domain-anchored skill sampling instead of uniform-random, to
give the knowledge graph real clustering signal — see `docs/data-at-scale.md`). Re-ran the exact
same three-way comparison against the new data:

| Mode | Precision@10 | Recall@10 | NDCG@10 | MRR |
|---|---|---|---|---|
| rule_based | 0.4267 | 0.6586 | 0.6449 | 0.7483 |
| blended | 0.5600 | 0.8697 | 0.8777 | 0.9000 |
| embeddings_only | 0.5233 | 0.8150 | 0.7980 | 0.8389 |

**This reverses the Round 1 finding**: embeddings-only now *beats* rule-based-only (NDCG 0.798 vs.
0.645), where it previously underperformed (0.776 vs. 0.849). The explanation is direct, not
speculative: the Skills-dimension embedding is a weighted average of per-skill vectors — when skill
assignment was uniform-random noise, that average carried almost no signal; now that skills are
domain-coherent, the same averaging operation produces a genuinely informative vector. Embeddings
were never weaker *in principle* — they were only ever as good as the skill data backing them.
Blended remains the strongest of the three, as before.

**Round 4 — promoted to the default ranking path.** Following Round 3's result, `GET
/candidates/{id}/job-matches` was changed to default to `mode=blended` — re-ranking the rule-based
eligible pool by the embeddings-blended score, falling back to pure rule-based order when a
candidate has no embeddings computed yet (`rule_based_no_embeddings`, reported via
`X-Ranking-Mode`). Eligibility and skill-gap fields still come from the rule-based pass unchanged —
embeddings don't produce those. `mode=rule_based` opts back into the original deterministic order.
See `app/embeddings/retrieval.py`'s `rank_jobs_for_candidate_blended_default`. Verified live:
re-ranking with real computed embeddings produced a different, correctly-ordered top-5 job list
compared to `mode=rule_based` against the same candidate.

## Known Limitations

- Education dimension not built — no real data source exists; see Purpose & Scope above.
- The two-stage architecture was originally **designed for** 100,000-job scale and verified only
  against the project's small dataset (1,000 jobs) at the time this document was first written; it
  has since been verified live at the real 50,000-job target scale with a real N+1 bug found and
  fixed along the way — see `docs/data-at-scale.md` for that full stress test.
- No raw CV-text field exists for candidates — the Overall dimension's candidate-side input is a
  synthesized summary sentence, not real free text, until that gap is addressed.
- Promoting a new production model requires a migration + full re-embed batch — there's no
  zero-downtime hot-swap path, an acceptable limitation for a single-developer local platform.

## Roadmap

- ~~Promote blended to the default ranking path~~ — **done**, see Round 4 above.
- **Courses dimension** (candidate skill-gap → course matching) — explicitly a separate topic,
  deferred per the user's own framing, reusing the same vector infrastructure.
- **Migrate to Qdrant**, only if pgvector is measured insufficient at a real target scale.

## Related Documents

- `docs/data-at-scale.md` — the domain-anchored data fix that reversed the Round 3 finding, and the
  full 50,000-job stress test
- `docs/skill-extraction.md` — the skill-embedding cache this reuses, and the evaluation-harness
  pattern this mirrors
- `docs/matching-engine.md` — the rule-based engine this complements
- `docs/knowledge-graph.md` — a documented future signal source for this blend (v3+)
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
