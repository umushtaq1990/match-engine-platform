# RAG Chatbot over the Knowledge Base

## Purpose & Scope

A chatbot answering questions about candidates, jobs, skills, courses, and behavioral analytics,
built explicitly to demonstrate RAG-engineering depth rather than a toy wrapper around an LLM:
retrieval (vector now, graph and hybrid as later phases), tool-calling orchestration, MLflow
tracing, persisted chat history/state, role-based document visibility, tag-based freshness, and a
golden-dataset eval harness measuring both retrieval metrics and LLM-judged answer quality
(relevance/coherence/faithfulness/groundedness).

**Phased, deliberately**: this document covers **Phase 1 only** — a complete, measured pipeline
using vector retrieval alone. Phase 2 (graph retrieval) and Phase 3 (hybrid retrieval +
cross-encoder reranking + a systematic model/parameter sweep) are designed-for by this phase's
schema and graph shape, not built yet — see Roadmap.

**A reference implementation the user pointed to during planning
(`Sanabil_POC`) turned out not to be RAG at all** — no vector store, no chunking, no LangChain/
LangGraph, no eval harness; it's a document-stuffing chatbot (the whole knowledge base concatenated
into the system prompt) with LangSmith tracing. Nothing architectural was reused from it; this
phase was built from this project's own existing patterns instead (see below).

## Architecture & Design

**A fully decoupled package, not a feature wired into the drilldown app.** `app/chatbot/` has zero
import from `app.golden.drilldown` or any other presentation layer — the drilldown app imports
*from* it, the same relationship it already has with `app.matching`/`app.embeddings`. This is what
makes the package liftable into another project as-is, per the brief's explicit requirement.

**Reused directly, not reinvented**:
- `app.embeddings.models.EntityEmbedding` — chunk embeddings are stored here with
  `entity_type="document_chunk"`, `dimension="chunk"`, reusing that table's own documented generic
  design rather than a parallel vector column.
- The `@mlflow.trace` pattern (`app.extraction.ollama_client`/`pipeline.py`) — the chat graph's
  root span is `@mlflow.trace(span_type=SpanType.CHAIN)`, identical to `extract_skills()`'s.
  `app.extraction.ollama_client.generate()`/`embed()` are called unmodified.
- `app.extraction.pipeline`'s `response_format` JSON-schema trick — tool selection and LLM-judge
  scores both use this, not
  free-text parsing, since `qwen2.5:1.5b` has no native function-calling API.
- The eval-harness shape (`app.embeddings.run_embedding_eval`/`app.evaluation.run_evaluation`) —
  MLflow for run-level aggregates (best-effort, never breaks a real run), Postgres
  (`ChatEvalResult`) for per-question drill-down detail.
- `app.evaluation.metrics`'s `precision_at_k`/`recall_at_k` — reused directly for retrieval
  grading, not reimplemented.
- The freshness discipline from `EntityEmbedding.source_hash`/`app.golden.models.
  GraphNarrative.facts_hash` — `DocumentChunk.content_hash`, recomputed and compared, not a
  time-based guess, is this project's third use of the same pattern.

**A design correction made during implementation, not assumed from the plan**:
`app.embeddings.compute_embeddings`'s `ENTITY_ID_QUERIES`/`DIMENSION_HASHERS` dicts are the
sanctioned extension point for a *new entity type that shares candidate/job's three dimensions*
(skills/experience/overall) — a document chunk has exactly one dimension of its own, so plugging
it into that loop would call e.g. `skills_source_hash(session, "document_chunk", chunk_id)` and
raise. `app/chatbot/chunk_embeddings.py` is instead its own small, independent refresh function —
same hash-then-skip discipline, `EntityEmbedding` reused as-is, but not forced into a loop shaped
for a different problem.

**LangGraph, two tools, deliberately** (`graph.py`/`tools.py`) — the actual "connecting two tools"
RAG-skill demonstration, not a fixed retrieve→generate chain:
1. `search_knowledge_base` → `retrieval.py`'s vector search, for open-ended/descriptive questions.
2. `lookup_structured_data` → a direct query against `app.ingestion.source_models` (not
   `app.golden.drilldown.data`'s loaders — that module is the drilldown app's own presentation
   layer; importing it here would re-couple this otherwise-portable package to one UI), for
   exact-value questions a vector search answers poorly (e.g. "candidate 42's exact years of
   experience").

Graph shape: `decide_tools → execute_tools → generate_answer → persist`. Linear today,
deliberately built as a graph (not a plain function chain) so Phase 2/3 can add a real routing
decision (vector vs. graph vs. hybrid) without a rewrite.

**Role-based visibility** (`access_control.py`): `ALLOWED_VISIBILITY_BY_ROLE` gates
`DocumentChunk.visibility` (`"public"`/`"confidential"`) by role (`admin`/`candidate`/`employer`),
enforced inside `retrieval.py`'s query itself, not the UI layer — an unrecognized role fails
*closed* to public-only, not open. A new `"admin"` Keycloak realm role
(`docker/keycloak/jme-realm.json`) backs this; `app/chatbot/provision_admin.py` is a one-off
bootstrap script (not a self-serve admin UI — proportionate for a single-developer platform).

**Login added to exactly one page**, not the whole drilldown app. The topics this chatbot covers
(candidates/jobs/skills/courses/behavioral analytics) match the drilldown app's existing pages
exactly, but that app has no authentication at all — and role-based visibility needs a real logged-
in user to check. `app/golden/drilldown/auth.py` gates only `pages/9_Chatbot.py`, reusing
`app.frontend.oidc.password_grant_login` unmodified rather than retrofitting auth onto the other
seven already-working pages.

## Implementation

| Component | File | Notes |
|---|---|---|
| Data models | `app/chatbot/models.py` | `DocumentChunk`, `ChatSession`, `ChatMessage`, `ChatEvalResult` |
| Ingestion | `app/chatbot/ingestion.py` | Builder-registry per `source_type`, mirrors `text_synthesis.py`'s pattern |
| Chunk embeddings | `app/chatbot/chunk_embeddings.py` | Independent refresh loop, see the design correction above |
| Retrieval | `app/chatbot/retrieval.py` | pgvector ANN + visibility filter |
| Access control | `app/chatbot/access_control.py` | Role → allowed visibilities |
| Chat history | `app/chatbot/chat_history.py` | Postgres-backed, not `st.session_state` |
| Tools | `app/chatbot/tools.py` | `search_knowledge_base`, `lookup_structured_data` |
| Orchestration | `app/chatbot/graph.py` | LangGraph, tool-calling, MLflow root span |
| Eval | `app/chatbot/eval/` | `golden_questions.py`, `metrics.py` (LLM-judge), `run_chatbot_eval.py` (full scored run), `run_retrieval_eval.py` (fast, judge-free retrieval-only run) |
| LLM backend selection | `app/chatbot/llm_backend.py` | `resolve_generate_fn("ollama"\|"vllm")` — Ollama (CPU, serial, the live chat page's default) or vLLM (GPU, continuous batching, for parallel eval runs) |
| UI | `app/golden/drilldown/pages/9_Chatbot.py` + `auth.py` | The one login-gated page |

## Verification & Results

**Ingestion + embeddings, live**: a 15-candidate/15-job sample produced 211 chunks (15 jobs + 15
candidates + 81 skills + 100 courses); embedding computation took 17.6s for all 211 (first run —
idempotent on rerun via `content_hash`).

**Access control, a real negative test, not just the happy path**: the identical query ("Python
developer with cloud experience") as `role="candidate"` returned only public skill/course chunks;
as `role="admin"` it returned the same public chunks *plus* confidential candidate profile chunks.
Confirmed again through the full agent: asking "How many years of experience does candidate 1
have?" as `admin` correctly answered "24 years"; the identical question as `candidate` correctly
answered "not available in the provided information."

**Two real bugs caught live during this verification, not found later**:
1. `_TOOL_CALL_SCHEMA`'s `arguments` field has no per-tool structural constraint (Ollama's
   `format` schema only enforces the outer `{name, arguments}` shape) — a real call came back
   `{"candidate_id": "1"}` instead of the requested `{"entity_type": "candidate", "entity_id":
   1}`. Fixed with defensive key-alias normalization (`_normalize_lookup_args`) rather than
   trusting a small model's exact compliance, plus a tightened prompt as defense in depth, and a
   regression test (`tests/chatbot/test_graph.py`) locking the fix in.
2. (Carried from Phase 48, same discipline) grounded-generation prompts need an explicit
   instruction against editorializing a neutral fact into a deficiency — reapplied here in
   `graph.py`'s `_ANSWER_PROMPT`.

**The original eval-scope mismatch (flagged above as "fix this before trusting the numbers") was
found, root-caused with a live manual diagnostic, and fixed — not left as a known limitation.**
Precision@5/recall@5 of 0.01/0.05 on the first live run turned out to be real, not a harness bug:
a direct query against the pgvector index (bypassing the harness entirely, `EntityEmbedding.vector.
cosine_distance(query_vector)`) confirmed `all-minilm` embeds by semantic shape ("years of
experience," generic job-posting boilerplate) and carries ~zero weight for a literal id token like
"#2522" among thousands of near-identical-shaped chunks — vector search alone essentially cannot
answer an ID-specific question. `run_chatbot_eval.py` was rewritten to route every question through
the real agent (`graph.run_chat_turn`, `retrieval_mode="agent"`) instead of calling `retrieve_chunks`
directly, so the agent's own choice between `search_knowledge_base` and `lookup_structured_data` is
what gets measured — the same choice the live chat page makes. `lookup_hit` is detected by checking
the **resolved tool-call result** (the actual row `lookup_structured_data` returned) against the
expected id, not by pattern-matching the LLM's raw call arguments — correct regardless of whether
the agent looked an entity up by id or by name.

**Skill/course had no structured-lookup path at all until this pass** — they were always routed
through `search_knowledge_base`, same vector-search weakness as candidate/job ids. Extended
`lookup_structured_data` with `entity_name`-based lookup (skill/course are named in a question by
name/title, never by internal id, unlike candidate/job) — see Architecture above for the
`Course.title` non-uniqueness case, which fails honest (`{"ambiguous": True, "matches": [...]}`)
rather than guessing.

**A second real bug caught live, fixed the same way as the first**: the tightened "always use
`lookup_structured_data` for a named skill/course" prompt initially *broke* job lookups — "What
skills are required for job 2522?" started making the model emit `{"entity_type": "job",
"entity_name": "2522"}` instead of `entity_id`, because the new skill/course emphasis bled into any
question that happened to mention the word "skills." Caught via the same direct-diagnostic
technique (dumping raw `tool_calls` per question, not trusting the aggregate score alone), fixed by
scoping the prompt's `entity_name` instruction explicitly to skill/course only, plus a defensive
fallback in `_normalize_lookup_args`: a numeric `entity_name` on a candidate/job call is recovered
as `entity_id` rather than silently failing that lookup. Both directions locked in with regression
tests in `tests/chatbot/test_graph.py`.

**Faster iteration loop added**: `run_retrieval_eval.py` isolates precision@k/recall@k from the
four LLM-judge metrics — ~1 LLM call per question (the tool-decision call) instead of ~5, since
precision/recall depend only on which chunk/lookup the agent picked, not on a judged answer. Used
to iterate on the prompt fix above without paying for judge calls whose result couldn't change.
Deliberately not persisted to `ChatEvalResult` (that table's own docstring scopes it to a full
scored run) or to `ChatSession`/`ChatMessage` (no answer is ever generated).

**vLLM backend added for eval throughput** (`llm_backend.py`), reusing this project's existing
Ollama/vLLM swap pattern from CV/job-generation (`app.golden.vllm_client`): `--backend vllm
--max-workers N` runs N questions concurrently via a `ThreadPoolExecutor` against vLLM's continuous
batching, each worker opening its own `SessionLocal()` (a SQLAlchemy `Session` isn't thread-safe to
share). The live chat page still defaults to Ollama. Confirmed live that the LLM-judge *quality*
scores (relevance/coherence/faithfulness/groundedness) differ between backends — expected, since
vLLM here serves a different model (`Qwen2.5-3B-AWQ`) than Ollama's judge (`qwen2.5:1.5b`) — so
quality scores are only comparable within one backend, not across; precision@k/recall@k are
unaffected by this since they don't depend on judge-model choice.

**Final numbers, full agent-routed eval, vLLM backend, 60 questions (15 each candidate/job/
skill/course), k=5**:

| Category | Precision@5 | Recall@5 | Relevance | Coherence | Faithfulness | Groundedness |
|---|---|---|---|---|---|---|
| Overall | 0.92 | 0.98 | 2.87 | 2.60 | 1.31 | 1.50 |
| candidate_profile | 1.00 | 1.00 | 3.13 | 2.51 | 0.80 | 0.96 |
| job_description | 1.00 | 1.00 | 2.73 | 2.56 | 0.78 | 1.00 |
| skill | 1.00 | 1.00 | 4.33 | 3.13 | 2.93 | 3.33 |
| course | 0.63 | 0.90 | 1.93 | 2.53 | 2.07 | 2.17 |

Candidate/job/skill precision@5/recall@5 are all a clean 1.0 — the agent reliably resolves these to
an exact DB row via `lookup_structured_data` rather than approximating via vector search. Course is
the one category not at 1.0, and honestly so: of the 100 courses, 11 share a title with another
course (`Course.title` has no uniqueness constraint, unlike `Skill.name`), and
`lookup_structured_data` correctly refuses to guess among them — those questions fall back to the
weaker chunk-based scoring path by design, not a bug left unfixed.

**Tests**: `python -m ruff check .` clean repo-wide; `python -m pytest tests/ -q` — 352 passed
(11 new: `tests/chatbot/test_access_control.py`, `test_chat_history.py`, `test_graph.py`).
Migration (`0274832a3dd9`) autogenerated cleanly (exactly the 4 new tables, nothing else detected)
and applied against the real local Postgres.

## Known Limitations

- 11 of 100 courses share a title with another course (`Course.title` has no uniqueness
  constraint) — `lookup_structured_data` returns `{"ambiguous": True, "matches": [...]}` rather
  than guessing, so those specific course questions fall back to weaker chunk-based scoring. The
  honest fix is a uniqueness constraint or a disambiguating field (provider+title) upstream in
  `app.ingestion`, not something this package should silently paper over.
- The small model's tool-routing choice is sensitive to prompt wording in ways that need a live
  diagnostic to catch, not just the aggregate score — this pass caught and fixed two such
  regressions (skill questions defaulting to vector search; the fix for that then breaking job
  questions that happen to mention "skills") by dumping raw `tool_calls` per question. Any future
  prompt change to `_DECIDE_TOOLS_PROMPT` should be re-verified the same way, not just by the
  aggregate precision/recall number.
- LLM-judge quality scores (relevance/coherence/faithfulness/groundedness) are only comparable
  within one backend — vLLM and Ollama serve different judge models here, so a quality-score
  regression across a backend switch isn't necessarily a real regression; precision@k/recall@k
  don't have this caveat.
- No raw CV-text field exists anywhere in this schema (same gap `app.embeddings.text_synthesis`
  already flags) — candidate chunks are honestly synthesized from structured fields, not from CV
  prose. A question whose answer depends on CV narrative detail (not structured fields) can't be
  answered correctly yet.
- `provision_admin.py` is a one-off manual script, not a self-serve admin flow — proportionate for
  a single-developer platform, a real gap if this ever needed multiple admins.
- The small model occasionally calls two tools when one would do (observed live:
  `search_knowledge_base` called twice with a cosmetic "language" argument difference, or
  `lookup_structured_data` called twice identically) — harmless redundancy, not a correctness
  issue, not fixed this pass.

## Roadmap

- **Phase 2 — graph retrieval**: a `lookup_graph_context` tool reusing
  `app/golden/drilldown/graph_explainability.py`'s bounded-query pattern, added as a 3rd tool
  alongside the two above; same eval harness, `retrieval_mode="graph"` rows for direct comparison.
- **Phase 3 — hybrid retrieval + reranking + sweep**: combine vector+graph retrieval, add a
  reranking stage (library choice — `sentence-transformers` cross-encoder vs. an LLM-prompted
  reranker via the existing Ollama client — deliberately left open until this phase), then a
  systematic sweep over embedding model / LLM model / chunk size / top-k across all three
  retrieval modes on this phase's established metrics.
- The eval-harness scope mismatch flagged in an earlier pass (vector-only retrieval scored against
  ID-specific questions) is now fixed — `run_chatbot_eval.py`/`run_retrieval_eval.py` both route
  through the full agent, so Phase 2/3's `retrieval_mode="graph"`/`"hybrid"` rows will compare
  fairly against Phase 1's `"agent"` rows on the same basis.
- A uniqueness constraint (or provider+title disambiguation) on `Course.title` would close the one
  remaining precision gap (see Known Limitations) — worth doing before Phase 2/3 numbers are
  compared against Phase 1's course category specifically.

## Related Documents

- `docs/architecture/data-platform/knowledge-graph.md` — the graph-based-CF investigation and the
  `graph_explainability.py` pattern Phase 2's graph-retrieval tool will reuse.
- `docs/architecture/data-platform/embedding-matching.md` — the `EntityEmbedding`/pgvector ANN
  pattern this chatbot's retrieval reuses directly.
- `docs/architecture/application-services/skill-extraction.md` — the `response_format`
  JSON-schema and eval-harness patterns this feature mirrors.
- `docs/architecture.md` — overall system architecture.
- `docs/progress.md` — Phase 49, full build history.
