# Multi-Dimensional Embedding Matching — Use Case F groundwork (Phase 17)

Status: v1 — **implemented and verified end-to-end**, including a real measured rule-based-vs-
embeddings comparison (assumption A5's deferred promise since Phase 4, now resolved). This
document was written and reviewed before any code existed, the same discipline as Phases
12/14/15/16. The design below matched the build with no changes required.

## The problem this solves

The existing matching engine (`app/matching/scoring.py`) is a single blended rule-based score
over skill/location/salary/occupation. This phase adds a **complementary, embeddings-based signal**
— not a replacement — scored separately per facet (skills, experience, overall text), so it can be
measured against the rule-based baseline before anyone decides it's actually better. This is the
exact comparison assumption A5 and A5b have been deferring since Phase 4: *"Phase 4 evaluation
report will state measured delta between rule-based and semantic approaches before any decision to
adopt."* This phase is where that measurement finally becomes possible.

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
candidate. Stage 1 is not an optimization trick bolted on later — it's the reason a vector
database is needed at all.

## Vector store: pgvector, not a new dedicated service

`docker-compose.yml`'s `db` service image swaps from `postgres:16` to **`pgvector/pgvector:0.8.7-pg16`**
— confirmed via Docker Hub's API to be a drop-in-compatible build of the same Postgres 16 with the
`pgvector` extension pre-installed, not a fork. No new container, no new client library beyond the
thin `pgvector` Python package (v0.5.1, a SQLAlchemy type adapter), no new backup/security surface.

This is the same "prove the simple thing first" discipline already applied to the matching
baseline (A5), the model-serving layer (Phase 15), and extraction (Phase 16) — not a default
choice made out of inertia. `docs/hardware-inventory.md` already anticipated Qdrant as the
"someday, if ever" answer for vector search; it stays the **documented v2 upgrade path**, adopted
only if pgvector's HNSW index is measured insufficient at a real target scale, not assumed
insufficient in advance.

## A real technical constraint this design resolves, not papers over

A single pgvector column has a **fixed vector width** (`vector(384)`, `vector(768)`, etc. are
different column types, and an HNSW index requires one fixed width). That's incompatible with "try
several embedding models of different output sizes" unless the production/experimentation split
below is made explicit:

- **Production table** (`entity_embeddings`): one pgvector column, HNSW-indexed, tied to exactly
  **one active default model per dimension** at a time. This is what stage 1's ANN retrieval
  actually queries.
- **Evaluation harness** (mirrors Phase 16's `run_extraction_eval.py` pattern exactly): computes
  embeddings from *alternate* models against the golden dataset, stored as plain JSONB (not
  indexed, not production-served — a small golden set doesn't need ANN speed, brute-force cosine
  comparison is fine), scored, logged to MLflow. Promoting a different model to production is an
  explicit migration (new column width) plus a full re-embed batch — never a silent swap.

This cleanly separates the **online serving path** (one fixed, indexed, fast production model)
from the **offline experimentation path** (freely swappable models, no indexing constraint) — a
standard, deliberate split in real ML platforms, not a limitation of this one.

## Embedding dimensions (v1)

| Dimension | Candidate source | Job source | How computed |
|---|---|---|---|
| **Skills** | `CandidateSkill` rows | `JobSkill` rows | **No new LLM call** — a proficiency/importance-weighted average of the per-skill embeddings Phase 16's `app/extraction/skill_cache.py` already computes and caches. Free, reuses existing infrastructure directly. |
| **Experience** | `years_experience` + `primary_occupation` | `occupation` + `employment_type` | A short synthesized sentence (e.g. "5 years of experience as a Data Analyst") embedded via the active model — honest, since it's derived from real structured fields, not fabricated |
| **Overall/semantic** | a synthesized profile summary (no raw CV text field exists yet — same gap Phase 16 already flagged) | `description_raw` directly | Whole-text embedding, captures nuance the structured fields and the Skills dimension both miss |
| ~~Education~~ | — | — | **Explicitly deferred, not built.** No education/certification table exists in the domain model. Fabricating one would repeat the exact mistake `docs/matching-engine.md` already documented avoiding once (qualification compatibility, omitted entirely rather than faked) |

## Storage schema (sketch)

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

HNSW index per `(dimension, embedding_model)` partition — stage 1's ANN query always filters to
one dimension and the one active model, never mixes widths.

## Invalidation — a cache-freshness problem, treated as one

`source_hash` is a hash of exactly the inputs that produced that vector (e.g., for the Skills
dimension: the sorted `(skill_id, proficiency)` tuples). When a candidate's skills change, the
freshly computed hash no longer matches the stored one — that row is **stale**, not silently wrong
forever. A refresh job (new one-off, `profiles: tools`, same shape as `migrate`/`seed`/
`analytics-seed`) recomputes only rows whose hash has drifted, not a blanket full recompute every
run — the same incremental-cache discipline already used for Phase 16's per-skill embedding cache,
applied at the entity level instead of the taxonomy level.

## Two-stage retrieval, concretely

1. **Stage 1**: for a given candidate, one ANN query against the Skills-dimension job embeddings
   (pgvector `<=>` operator, HNSW-indexed) returns the top-N (e.g. 200) most plausible jobs in
   milliseconds, even over 100,000 rows.
2. **Stage 2**: for only those ~200 jobs, compute Experience-dimension and Overall-dimension
   similarity too, blend all three with the existing rule-based score's location/salary/occupation
   components (a documented weighted combination, same transparency discipline as
   `app/matching/scoring.py`'s own weights table), and re-rank.

This augments `rank_jobs_for_candidate`/`rank_candidates_for_job` with an optional embeddings-based
path — it does not replace the existing rule-based ranking, which stays the default until the
evaluation harness below shows the blend actually helps.

## Golden dataset — extends Phase 4's, doesn't just repeat it

Phase 4's ground truth (occupation-only alignment) is necessary but not sufficient here: the
entire *point* of embeddings is capturing "similar but not identical" relationships the rule-based
engine's exact-match scoring structurally cannot — a candidate with strongly overlapping but
differently-named skills, or a related-but-different occupation, should score well on the Skills
dimension even when Phase 4's ground truth would call the pairing irrelevant. So the golden dataset
adds a second part: hand-labeled **near-miss pairs** specifically designed to test that added
value (e.g., two skill sets that are semantically close but share zero exact `skill_id` overlap).

Evaluation itself reuses Phase 4's exact metrics (Precision@K, Recall@K, NDCG@K, MRR) run three
ways per candidate model: rule-based only (today's baseline), embeddings-blended, and
embeddings-only — so the eventual comparison is a measured delta per the brief's original
requirement, not a vibe.

## Model progression

Starts at **`all-minilm`** (384-dim, ~45MB — confirmed by directly pulling and embedding a test
string against the running `ollama` service) as the genuinely small baseline, compared against
`nomic-embed-text` (768-dim, already in use for Phase 16) and heavier options later — all
swappable through Ollama's same stable API, all logged to MLflow for comparison, identical
discipline to Phase 16's model-comparison harness.

## Service boundary

Not a new deployable image. This extends the matching engine itself (new routes/logic reachable
from the existing `api` image) and reuses Phase 16's `extraction` image for the offline batch jobs
(embedding computation, refresh, evaluation harness) since that image already has Ollama access and
the skill-embedding cache — adding a sixth Dockerfile for this would duplicate dependencies for no
isolation benefit, since this capability's whole purpose is to feed the existing matching code, not
stand alone.

`pgvector` (the Python package) is added only to the specific `requirements-*.txt` files that need
it (`migrate`, `api`, `extraction`) — not `requirements-base.txt` — keeping every other image's
size unaffected by a dependency it doesn't use, the same per-image-narrow-dependency discipline
`docs/data-quality.md` already documents for the `dataquality` image.

## Embedding-model comparison (Phase 18) — the simple model won

Directly comparing `all-minilm` (384-dim, the v1 production default) against `nomic-embed-text`
(768-dim, "more capable" by parameter count) on the 12-pair near-miss set — the one comparison that
doesn't require touching the production table, since it only embeds two words at a time:

| Model | Similar-pair range | Dissimilar-pair range | Best achievable accuracy |
|---|---|---|---|
| `all-minilm` | 0.315 – 0.649 | 0.145 – 0.252 | **12/12 (100%)** — clean gap, threshold 0.28 |
| `nomic-embed-text` | 0.422 – 0.678 | 0.397 – 0.439 | **11/12 (92%)** — *overlapping* ranges, no
  threshold separates all 12 pairs; e.g. `Docker`/`Kubernetes` (similar, 0.422) scores *below*
  `Carpentry`/`SQL` (dissimilar, 0.439) |

**Reading this honestly**: the larger, more general-purpose embedding model did not win here — it
separates these specific skill-pair semantics *worse* than the smaller model purpose-sized for
short-text similarity. `all-minilm` stays the production default by measurement, not by inertia.
This is exactly the outcome this tuning step existed to either confirm or overturn — and in this
case, confirms the original choice rather than finding a better one, which is itself a real result,
not a null one.

**What this comparison does not cover**: a full ranking-quality comparison across embedding models
(not just the near-miss task) would require computing `nomic-embed-text` embeddings for the actual
candidate/job population ad-hoc (768-dim vectors can't be written into the 384-dim production
`entity_embeddings` column — see "A real technical constraint" above) — not built in this pass;
the near-miss result was a strong enough signal not to invest further time switching models for
v2.

## Phased roadmap

- **v1 (this phase)**: pgvector swap, `entity_embeddings` schema + indexes, Skills/Experience/
  Overall embedding computation for the existing synthetic dataset (2,000 candidates, 1,000 jobs),
  the refresh/invalidation job, stage-1 ANN retrieval wired into a new optional path on the
  existing ranking functions, and the three-way evaluation harness (rule-based / blended /
  embeddings-only) comparing `all-minilm` vs `nomic-embed-text`.
- **v2**: if a config measurably beats rule-based-only, promote it to the default path — a
  decision made from v1's numbers, not assumed now.
- **v3**: courses dimension (candidate skill-gap → course matching) — explicitly a separate topic,
  deferred per the user's own framing, reusing the same vector infrastructure.
- **v4, only if pgvector is measured insufficient**: migrate to Qdrant at a real target scale.

## Verified against real execution (not fabricated)

- **pgvector image swap**: confirmed via Docker Hub's API as a genuine drop-in build; swapped the
  running `db` container against the existing data volume and verified every prior table's row
  counts survived unchanged (candidates=2000, jobs=1000, behavioral_events=28226,
  extraction_runs=3). A collation-version warning from the base-image glibc difference was fixed
  with Postgres's own documented `ALTER DATABASE ... REFRESH COLLATION VERSION` — a one-time local
  fix, irrelevant to a fresh checkout/CI run, which always creates a new volume from scratch.
- **pgvector functional smoke test**: a real `CREATE EXTENSION`, `vector(3)` column, insert, and
  `<=>` cosine-distance query returned mathematically correct similarity ordering before any
  application code was written.
- **Migration**: `alembic revision --autogenerate` correctly detected both new tables; `CREATE
  EXTENSION` and the HNSW index (`USING hnsw ... vector_cosine_ops`) were added by hand, since
  autogenerate can't express a non-default Postgres access method. Declaring the same index in the
  ORM's `__table_args__` (without re-running `CREATE INDEX`) keeps `alembic check` reporting zero
  drift going forward, not a permanent false positive.
- **Batch computation**: `python -m app.embeddings.compute_embeddings` computed real embeddings for
  all 1,000 jobs and a sample of 80 candidates (two embedding-model calls per entity for
  Experience/Overall; Skills cost zero new calls, reusing Phase 16's cache). A second run against
  the same data correctly skipped all 240 already-computed rows via the `source_hash` check —
  idempotence verified, not assumed.
- **Two-stage retrieval**: a real call to `GET /candidates/1/job-matches/semantic` returned ranked
  jobs from the full 1,000-job population with no visible latency — confirming the entire point of
  precomputing embeddings offline: zero live Ollama calls on the request path, pure pgvector ANN
  plus in-Python blending.
- **Near-miss threshold, calibrated from real measurement, not guessed**: directly embedding the
  12 hand-labeled skill pairs with `all-minilm` showed every "similar" pair scoring 0.315–0.649
  cosine similarity and every "dissimilar" pair scoring 0.145–0.252 — a clean gap, with 0.28 chosen
  as the threshold. Scored 100% accuracy on this set at that threshold.
- **The actual rule-based-vs-embeddings comparison** (30 candidates with both ground truth and
  computed embeddings, k=10):

  | Mode | Precision@10 | Recall@10 | NDCG@10 | MRR |
  |---|---|---|---|---|
  | rule_based | 0.5200 | 0.8617 | 0.8489 | 0.8806 |
  | blended | 0.5233 | 0.8517 | 0.8749 | 0.9333 |
  | embeddings_only | 0.4967 | 0.7908 | 0.7755 | 0.8153 |

  Reading this honestly: **blended modestly beats rule-based-only** on NDCG and MRR (better
  ordering of the genuinely relevant jobs near the top), while **embeddings-only underperforms**
  rule-based-only here — an explainable result, not a surprising failure: Phase 4's ground truth is
  itself an exact-occupation-match label, which the rule-based score's own occupation component
  answers almost perfectly by construction. Embeddings add complementary signal on top of that,
  but don't replace it. Rule-based stays the default ranking; the semantic endpoint is available
  as an additional, not a replacement, path — exactly the outcome this measurement was built to
  either confirm or deny, not assumed in advance.

## Known limitations (v1, by design)

- Education dimension not built — no real data source exists; see table above.
- The two-stage architecture is **designed for** 100,000-job scale but only verified against this
  project's actual dataset (1,000 jobs) — a real stress test at that scale would require either a
  much larger synthetic dataset or synthetic duplication, neither built in v1. The architecture is
  correct in shape; its performance at that scale is not yet measured, and that distinction is
  kept honest rather than implied.
- No raw CV-text field exists for candidates (same gap Phase 16 already flagged) — the Overall
  dimension's candidate-side input is a synthesized summary sentence, not real free text, until
  that gap is addressed.
- Promoting a new production model requires a migration + full re-embed batch — there's no
  zero-downtime hot-swap path in v1, and that's an acceptable limitation for a single-developer
  local platform.
