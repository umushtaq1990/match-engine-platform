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
| Eval | `app/chatbot/eval/` | `golden_questions.py`, `metrics.py` (LLM-judge), `run_chatbot_eval.py` |
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

**Eval harness, real numbers, honestly reported including the weak ones**: a live run (10
candidates, 10 jobs, k=5) scored:

| Metric | Score |
|---|---|
| Relevance | 4.7 / 5 |
| Coherence | 4.0 / 5 |
| Faithfulness | 3.8 / 5 |
| Groundedness | 2.05 / 5 |
| Precision@5 | 0.01 |
| Recall@5 | 0.05 |

Reading this honestly: relevance/coherence look strong, but precision@5/recall@5 are close to
zero — vector search rarely retrieves the *exact* chunk these golden questions need. This is a
**measurement-scope mismatch, not a pipeline bug**: the golden questions are ID-specific
("candidate 1's years of experience"), which pure semantic vector search is inherently weak at
(embeddings capture meaning, not exact numeric IDs) — exactly why `lookup_structured_data` exists,
and confirmed separately (above) that the full agent correctly routes these questions to it and
answers correctly. `run_chatbot_eval.py` currently exercises `retrieve_chunks` directly, bypassing
the agent's tool-routing — so it measures vector-only retrieval against questions vector retrieval
was never meant to answer alone. Groundedness (2.05) is the metric that actually caught the
consequence of this — relevance/coherence alone would have painted a falsely rosy picture, a real
demonstration of why a RAG eval needs more than one metric. Fixing the mismatch (split the golden
set by question type, or evaluate the full agent instead of raw retrieval) is Known Limitations
below, not silently patched over.

**Tests**: `python -m ruff check .` clean repo-wide; `python -m pytest tests/ -q` — 352 passed
(11 new: `tests/chatbot/test_access_control.py`, `test_chat_history.py`, `test_graph.py`).
Migration (`0274832a3dd9`) autogenerated cleanly (exactly the 4 new tables, nothing else detected)
and applied against the real local Postgres.

## Known Limitations

- `run_chatbot_eval.py` scores raw vector retrieval, not the full tool-calling agent — see the
  eval-results discussion above. A fairer eval would run `graph.run_chat_turn` per question (which
  correctly uses `lookup_structured_data` for ID-specific questions) or split the golden set by
  question type and score each against the retrieval mode actually suited to it.
- No raw CV-text field exists anywhere in this schema (same gap `app.embeddings.text_synthesis`
  already flags) — candidate chunks are honestly synthesized from structured fields, not from CV
  prose. A question whose answer depends on CV narrative detail (not structured fields) can't be
  answered correctly yet.
- `provision_admin.py` is a one-off manual script, not a self-serve admin flow — proportionate for
  a single-developer platform, a real gap if this ever needed multiple admins.
- The small model (`qwen2.5:1.5b`) occasionally calls two tools when one would do (observed live:
  `search_knowledge_base` called twice with a cosmetic "language" argument difference) — harmless
  redundancy, not a correctness issue, not fixed this pass.

## Roadmap

- **Phase 2 — graph retrieval**: a `lookup_graph_context` tool reusing
  `app/golden/drilldown/graph_explainability.py`'s bounded-query pattern, added as a 3rd tool
  alongside the two above; same eval harness, `retrieval_mode="graph"` rows for direct comparison.
- **Phase 3 — hybrid retrieval + reranking + sweep**: combine vector+graph retrieval, add a
  reranking stage (library choice — `sentence-transformers` cross-encoder vs. an LLM-prompted
  reranker via the existing Ollama client — deliberately left open until this phase), then a
  systematic sweep over embedding model / LLM model / chunk size / top-k across all three
  retrieval modes on this phase's established metrics.
- Fix the eval-harness scope mismatch noted in Known Limitations before trusting Phase 2/3's
  numbers against Phase 1's.

## Related Documents

- `docs/architecture/data-platform/knowledge-graph.md` — the graph-based-CF investigation and the
  `graph_explainability.py` pattern Phase 2's graph-retrieval tool will reuse.
- `docs/architecture/data-platform/embedding-matching.md` — the `EntityEmbedding`/pgvector ANN
  pattern this chatbot's retrieval reuses directly.
- `docs/architecture/application-services/skill-extraction.md` — the `response_format`
  JSON-schema and eval-harness patterns this feature mirrors.
- `docs/architecture.md` — overall system architecture.
- `docs/progress.md` — Phase 49, full build history.
