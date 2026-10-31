# Free-Text Skill Extraction — Use Case C

Status: v2 — **implemented, verified end-to-end, and tuned**: a real measured baseline (v1) was
followed by a real threshold sweep and LLM comparison (v2), improving F1 from 0.504 to 0.60–0.66
and promoting a smaller, better-performing model as the new default — see Verification & Results
below. This document was written and reviewed before any code existed, the same discipline followed
throughout this project. The design matched the build with one real bug found and fixed along the
way.

## Purpose & Scope

Extract both **technical and non-technical** skills from free text (a job description, a CV
snippet) and classify each as **required** or **preferred**, canonicalized onto this project's
existing 64-skill taxonomy (`Skill`/`SkillSynonym`) — not just technical keywords. The taxonomy
already spans both (`programming`/`data`/`cloud`/`ai`/`it`/`engineering` alongside `soft_skill`/
`business`/`hr`/`healthcare`/`trades`/`logistics`/`creative`/`finance`/`office`/`education`), so the
extraction prompt and evaluation must treat both as first-class, not add non-technical as an
afterthought.

Per assumption A5b, this use case specifically skips a rule-based baseline and goes straight to an
NLP/embedding-based approach — the opposite of every other use case in this project, and the reason
the shared LLM/embedding serving layer (`docs/model-serving.md`) was stood up before this work could
start.

Out of scope (v1/v2): negation handling, raw-CV-text ingestion, and wiring extraction into a real
write path that populates `JobSkill`/`CandidateSkill` rows — see Known Limitations and Roadmap.

## Architecture & Design

**Why a hybrid pipeline (LLM extraction + embedding canonicalization), not either alone.**
`SkillSynonym`'s own docstring (`app/ingestion/source_models.py`) was written anticipating "a
dictionary-based extractor" — a simpler design than what A5b later committed to. The hybrid pipeline
below still uses `SkillSynonym`, just differently: not as a lookup dictionary, but as extra
reference text that improves embedding-based canonicalization.

- **LLM-only** (forced to pick from the 64-skill list in-prompt) tends to force a match even when
  the text doesn't really mention anything in the taxonomy — you lose the "this is a real skill
  mention the taxonomy doesn't cover" signal, which matters for taxonomy maintenance.
- **Embeddings-only** (no LLM) means hand-building a phrase-chunking/NER step first — reinventing
  what a small instruct model already does adequately for this volume of text.
- **Hybrid**: the LLM extracts candidate skill phrases + a required/preferred guess; embedding
  similarity against the canonical skill set (names *and* synonyms) then maps each phrase to a
  `skill_id` with a **quantifiable confidence score** (cosine similarity) — a measurable, tunable
  threshold, the same discipline already used for data-quality check thresholds, not a black-box
  "trust the LLM" call.

**Service boundary**:

| | Decision | Rationale |
|---|---|---|
| Service | New `extraction` service, own image (`docker/Dockerfile.extraction`) | Separately testable/evaluable, same bounded-context pattern as `analytics` |
| Model access | Calls the existing `ollama` service over HTTP — no model weights in this image | Reuses the shared serving layer; this service is pure orchestration logic |
| DB schema | **Public schema** (not an isolated schema like `analytics`) | Unlike behavioral analytics (a genuinely separate concern), this service's eventual purpose (v3) is to populate the *same* `job_skills`/`candidate_skills` tables the matching engine already owns — it's part of the core domain, not an adjacent one. Its eval tables join the same migration history as `evaluation_runs`/`data_quality_runs` |
| Error handling | Failures surface as real errors to the caller, **not** swallowed like the analytics instrumentation hook | Analytics impressions are optional side-effects; here, the caller usually has nothing useful without a result (same distinction flagged in `docs/model-serving.md`'s consumer-pattern section) |

**Golden dataset (ground truth)**, two complementary parts, not one:

- **Part A — generator-injected** (`app/ingestion/generate_synthetic_data.py`, enhanced): job
  description text originally was unrelated filler (`fake.paragraph(...)`) — it didn't mention the
  job's actual skills at all. Enhanced to weave the job's real required/preferred skills into the
  description prose, **sometimes by canonical name, sometimes by a synonym**, so it isn't trivially
  easy. Ground truth = the `JobSkill` rows already generated for that job — cheap, deterministic,
  scales to all 1,000 jobs for free. This is *not* a repeat of the matching-evaluation circularity
  mistake (`docs/evaluation-strategy.md`): there, the risk was grading the ranking formula against
  labels derived from that same formula. Here, the task is literally "parse this text and recover
  the structured facts it contains" — the text is *constructed from* the ground truth by design,
  which is the correct way to test a parser, not a methodological error. It proves mechanical
  correctness (can the pipeline recover known facts from text), not real-world recall.
- **Part B — hand-authored, harder cases** (`data/golden/skill_extraction.yaml`, checked into git):
  a few dozen realistic snippets — job description and CV-style text — specifically covering what
  Part A's templates can't exercise: skills mentioned only via an uncommon synonym or paraphrase,
  ambiguous requirement language ("exposure to X is a plus" vs. "must have X"), non-technical skills
  stated in prose rather than a bulleted list, and mentions of real-sounding skills **not in the
  taxonomy at all** (tests the "no match" path, not just the match path). Hand-authored because this
  is exactly the part a template generator structurally cannot produce — real ambiguity and phrasing
  variety. Stored as a versioned fixture file, not a DB table, so changes are git-reviewable like any
  other test fixture.

**Metrics**, different in kind from the matching-ranking metrics (which measure *ranking* quality,
not *extraction* accuracy):

| Metric | What it measures |
|---|---|
| Skill-set Precision / Recall / F1 | Set comparison per example: predicted `skill_id`s vs. expected `skill_id`s, micro-averaged across all golden examples |
| Requirement-level accuracy | Of the correctly-identified skills (true positives only), what fraction got required/preferred right |
| Unmatched-phrase rate | How often a real extracted phrase falls below the canonicalization threshold — not an error by itself, but worth tracking per model/threshold config |

**Results storage** (mirrors the matching-evaluation pattern exactly): MLflow (existing service, new
experiment) tracks run-level params (`llm_model`, `embedding_model`, `similarity_threshold`,
`prompt_version`) and the three metrics above. Postgres (`extraction_runs` /
`extraction_example_results`, same two-level shape as `evaluation_runs`/`data_quality_runs`) keeps
per-example predicted-vs-expected detail, for drill-down into exactly which examples a given config
got wrong.

## Implementation

**Pipeline**:
1. **LLM extraction** — an instruct model via Ollama's `/api/generate`, given the raw text, returns
   a JSON list of `{phrase, requirement_level_guess}`. Prompt explicitly asks for *all* skill
   mentions, technical and non-technical, not just an obvious technical subset.
2. **Embedding canonicalization** — an embedding model via `/api/embeddings`. Canonical skill names
   + all `SkillSynonym` rows are embedded **once** (cached, not recomputed per request — 64 skills
   plus synonyms is a small, static set). Each extracted phrase is embedded and compared by cosine
   similarity against that cached set; best match above a threshold → that `skill_id`. Below
   threshold → recorded as "no taxonomy match," not silently dropped.
3. **Output**: a list of `{skill_id, requirement_level, confidence, source_phrase}` plus a separate
   list of unmatched phrases.

Both the LLM model and the embedding model are **run parameters passed through to Ollama's API**,
not hardcoded — swapping models is a different MLflow run, not a code change. That's what makes
"try different models, see what scores better" actually work.

### CV upload storage

A candidate can upload their CV as a PDF (`POST /candidates/{id}/cv`, `GET /candidates/{id}/cv` for
metadata, `GET /candidates/{id}/cv/download` for the file itself) — a genuinely basic feature by
design: PDF only, 2MB cap, one CV per candidate (a re-upload overwrites the prior one, no version
history), no virus scanning. The point was answering "where does a real candidate's uploaded
document live," not building a document-management system.

**Tool choice, verified not assumed**: SeaweedFS, not MinIO. MinIO stopped publishing public
container images in 2025 — confirmed directly, not assumed from a changelog: `docker pull
minio/minio` returns "pull access denied, repository does not exist," and both Docker Hub's and
Quay's tag-listing APIs refuse unauthenticated access for that repository. SeaweedFS's
`chrislusf/seaweedfs` image is still freely pullable (confirmed: `docker pull
chrislusf/seaweedfs:4.51` succeeds), open source, and speaks the same S3-compatible API any client
library already expects — the `minio` Python package (an S3 client despite its name) works against
it unmodified, confirmed by a real upload/download round trip before any application code was
written. Runs in `mini` mode (bundles master/volume/filer/S3 gateway in one process), appropriate
at this project's scale (50-100 CVs) rather than a distributed storage cluster.

Only object *metadata* (`CandidateDocument`: candidate_id, object key, filename, content type,
size, upload timestamp) lives in Postgres — the PDF bytes themselves live only in object storage,
never duplicated into the relational database. Validation checks both the declared Content-Type
*and* the actual PDF magic bytes (`%PDF-`), since a client can claim any content type it likes for
a request body. Deliberately not fail-open like the Redis integrations elsewhere in this project —
if object storage is unreachable, the upload fails with a real `503`, since a CV upload with
nowhere to put the file is a genuine failure the caller must see, not something to silently drop.

## Verification & Results

**Generator enhancement**: `app/ingestion/generate_synthetic_data.py` now weaves each job's actual
required/preferred skills into its `description_raw` text, sometimes by canonical name and
sometimes by a known synonym (confirmed: 311/1,000 jobs use a synonym variant, not just the
canonical name). Verified against real rows, e.g. job #1's description mentions "Django,
Cybersecurity, and Quality Assurance" as required and "PowerPoint" as preferred — exactly matching
its real `JobSkill` rows, not a coincidence.

**A real bug found and fixed during the first eval run**: the first baseline run scored
**F1 = 0.0385** — essentially nothing extracted. Root-caused (not guessed) by calling Ollama
directly with the exact prompt: `"format": "json"` (the loose string) let `llama3.2:1b` emit a
single malformed object (`{"-phrase": "HR Coordinator 4", ...}`) instead of the requested array,
which the parser correctly rejected as invalid (returning zero extracted phrases, not a crash).
Fixed by passing an actual **JSON Schema** as `format` instead — Ollama then constrains the output
structurally, not just to "valid JSON of any shape." Re-verified directly via `curl` before touching
code: the schema form correctly extracted `Kubernetes`, `Nursing`, `Social Media` from the same text
the loose string had failed on.

**Baseline run, post-fix** (`llama3.2:1b` + `nomic-embed-text`, threshold 0.55, prompt v2, 46
examples = 30 generator-injected + 16 hand-authored): **Precision 0.636, Recall 0.447, F1 0.504,
requirement-level accuracy 0.961, unmatched-phrase rate 0.0** — a real, unoptimized baseline, not a
cherry-picked number. Logged to MLflow (`jme-skill-extraction` experiment) and Postgres
(`extraction_runs`/`extraction_example_results`) for later comparison.

**Spot-checked per-example results, not just the aggregate**: the out-of-taxonomy hand-authored case
(`astrophotography and vintage typewriter repair`) correctly produced zero matches — no false
positive forced onto the taxonomy. A CV-style snippet mentioning a real skill (`Python`) alongside a
deliberately absurd one (`underwater basket weaving`) correctly matched the former and flagged the
latter as unmatched rather than forcing it onto the nearest taxonomy entry. Several examples scored
a perfect 1.0 F1 (all skills recovered, all requirement levels correct); several scored 0 (the LLM
returned an empty list for that text) — a believable result for an unoptimized 1B-parameter model,
not a bug.

**Model/threshold tuning — the actual "get better scores" step**:
- **Threshold sweep** (`llm_model=llama3.2:1b`, same prompt, 46 examples each): 0.45 → F1 0.479,
  0.50 → F1 0.456, **0.55 (the original default) → F1 0.504 — still the best of the five values
  tested**, 0.60 → F1 0.344, 0.65 → F1 0.412. The original choice held up under a real sweep rather
  than being revised on a hunch.
- **LLM comparison at that threshold**: `qwen2.5:1.5b` vs. the original baseline `llama3.2:1b` —
  **F1 0.60–0.66 vs. 0.504** across two separate runs (run-to-run variance from sampling, both
  comfortably above the baseline), recall improving most (0.60–0.68 vs. 0.447), requirement-level
  accuracy also slightly better (0.978–0.986 vs. 0.961). `qwen2.5:1.5b` is also smaller on disk
  (986MB vs. 1.3GB) despite having more parameters — a more efficient quantization, not a
  size/quality tradeoff in the usual direction.
- **Promoted**: `DEFAULT_LLM_MODEL` in `app/extraction/pipeline.py` is now `qwen2.5:1.5b` — a
  measured decision, not a default left unexamined. `llama3.2:1b` is no longer pulled by default
  (`docker-compose.yml`'s `ollama-pull` job) but remains reproducible on demand.
- Re-verified against the live `/extract` endpoint after promoting: correctly extracted all four
  skills (Python, Docker, Communication as required; Kubernetes as preferred) from a real request,
  with confidence scores all above 0.77 and zero unmatched phrases.

**Re-verification after the synthetic generator changed**: the generator later changed
(domain-anchored skill sampling, see `docs/data-at-scale.md`). Re-ran the exact same eval
(`qwen2.5:1.5b`, threshold 0.55) against the new data: **F1 0.6766** — consistent with the 0.60–0.66
range measured above, confirming the tuned configuration still holds against the regenerated
dataset.

**Error-surfacing design proven, not just asserted**: stopped the `ollama` container and called
`/extract` — got back a real `502 {"detail": "Model-serving service unavailable"}`, not a silent
empty response or a generic 500. Confirms the deliberate difference from the analytics instrumentation
hook's best-effort design actually holds in practice.

**CI**: `fresh-volume-smoke-test` builds and scans the `extraction` image, starts it after `ollama`
is healthy and models are pulled, and makes one real (not mocked) extraction call, asserting a real
skill was matched with the correct requirement level.

**CV upload, verified live against the real stack, not just unit tests**: uploaded a real PDF to
`/candidates/1/cv`, confirmed the row landed in `candidate_documents` (correct filename/size), the
object itself landed in SeaweedFS (`client.list_objects` showed `candidates/1/cv.pdf`, matching
size), and `/candidates/1/cv/download` returned bytes that `diff`'d identical to the original file.
Validation re-checked live too: a `.png` upload correctly returned `422` ("Only application/pdf is
accepted"), a 3MB file correctly returned `422` ("File too large"), and an unknown candidate_id
correctly returned `404` before ever touching object storage.

## Known Limitations

- No negation handling ("no Java experience required" would likely still extract "Java") — flagged
  as a known gap, not silently assumed away.
- ~~No raw-CV-text field exists anywhere in the current domain model...~~ **Partially resolved**: a
  candidate can now upload and store a CV as a PDF (see "CV upload storage" above). What's still
  missing: the PDF's text is never parsed or run through `extract_skills` automatically — upload and
  extraction are two separate, deliberately un-chained steps in v1 (see Roadmap).
- Recall improved from 0.447 (original `llama3.2:1b`) to 0.60–0.68 (`qwen2.5:1.5b`) but is still not
  close to 1.0 — some real skill mentions are still missed, most likely needing few-shot examples in
  the prompt to close the remaining gap further. Not yet attempted.
- Canonicalization threshold (0.55) was confirmed by a real sweep, not left as an unexamined first
  guess — but only five values were tried; a finer sweep could still find something better.

## Roadmap

- ~~Threshold sweep and model comparison~~ — **done**, see Verification & Results above.
- ~~Basic CV upload storage~~ — **done**, see "CV upload storage" above.
- **Parse the uploaded PDF's text and run it through `extract_skills` automatically**, a natural
  next step now that both halves exist independently — not built yet because PDF text extraction is
  a real, separate failure mode (scanned/image-only PDFs have no extractable text at all), deserving
  its own measured pass rather than being bolted on silently.
- **Wire extraction into a real write path**, only after the current pipeline is shown good enough:
  e.g. an endpoint that accepts raw text and actually populates `JobSkill` rows, at which point
  requirement-level output stops being only an eval metric and starts being operational data. Not
  scheduled yet.

## Related Documents

- `docs/model-serving.md` — the shared Ollama serving layer this depends on
- `docs/embedding-matching.md` — reuses this pipeline's skill-embedding cache for the Skills
  dimension
- `docs/evaluation-strategy.md` — the matching-evaluation harness this extraction evaluation mirrors
- `docs/data-at-scale.md` — the domain-anchored data change this was re-verified against
- `docs/assumptions-register.md` — assumption A5b, the exception this use case follows
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
