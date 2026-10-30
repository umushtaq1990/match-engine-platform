# Multi-Dimensional Embedding Matching — Use Case F groundwork (Phase 17, design)

Status: v0.1 — **design only, not yet built**. Same discipline as Phases 12/14/15/16: written and
reviewed before any code exists.

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
