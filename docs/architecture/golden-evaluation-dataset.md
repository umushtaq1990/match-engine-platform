# Golden Evaluation Dataset

## Purpose & Scope

500 of the existing seeded candidates, each given a realistic LLM-written CV and three LLM-written job postings deliberately constructed to be a near-perfect skill match, a real Keycloak login, and a persona-driven behavioral history. One dataset, built once, feeding three separate evaluation needs that previously had no good fixture:

- **Skill-extraction eval** against real natural-prose text, not text where the ground truth is visible by construction (see "Why the existing synthetic data wasn't enough" below).
- **Recommendation eval** against a *known* right answer per candidate (does the matcher actually rank a candidate's three true matches highly?), not just the population-wide "same occupation somewhere in the pool" ground truth.
- **Clustering / business-insights groundwork**: `app/analytics/segmentation.py` already names this project's planned "v3" (*"Real clustering (k-means/HDBSCAN) stays v3, only if \[rule-based segmentation\] is shown insufficient"*) — this dataset is what that v3 needs: deliberate, controlled diversity across both skill/occupation and behavioral-engagement axes, so a later k-means pass finds real, separable structure instead of noise.

Explicitly **not** in scope here: the k-means/clustering analysis itself, and a live per-request traffic simulator (hitting the real authenticated API) — both are natural follow-ups once this dataset exists, not built as part of it.

### Why the existing synthetic data wasn't enough

Two pre-existing generators share the same weakness: the "ground truth" is visible directly in the generated text, not hidden in natural prose the way a real extraction pipeline would actually have to work.

- **CVs** (`app/extraction/generate_synthetic_cvs.py`): a templated FPDF built mechanically from a candidate's own DB fields — trivial for the extraction pipeline to parse because the text *is* the structured data.
- **Job postings** (`app/ingestion/generate_synthetic_data.py:_build_job_description`): Faker boilerplate plus one fixed template sentence naming the required/preferred skills verbatim.
- **Recommendation ground truth** (`app/evaluation/ground_truth.py:build_ground_truth`): purely structural, `Candidate.primary_occupation_id == Job.occupation_id` — no signal about *how well* a candidate's actual skills match a job's actual requirements.
- **Candidates and Keycloak logins were fully decoupled**: all 2000 seeded candidates had zero login capability (issue #26's ownership model never needed one — the seeded dataset is deliberately "unowned").

## Architecture & Design

### Candidate selection (`app/golden/select.py`)

`select_golden_candidate_ids(session, n=500)` is a **pure, deterministic** function over already-seeded data — not a stored selection. Every other script in this package calls it, so the exact same 500 ids are re-derived every time without a manifest file or an extra table. Candidates need a real `primary_occupation_id` and at least 3 `CandidateSkill` rows; eligible ids are sorted by `(primary_occupation_id, id)` and strided evenly across that ordering so the selection spreads across all 150 occupations (confirmed: all 150 represented, 1-6 candidates each) rather than favoring whichever occupation sorts first.

### Golden CVs (`app/golden/generate_cvs.py`)

For each selected candidate, an LLM prompt (`qwen2.5:1.5b` via the existing `app/extraction/ollama_client.py`) writes a natural one-page resume from that candidate's real ground truth (name, years of experience, occupation, skills+proficiency) — explicitly instructed to avoid a rigid template or a bare "Skills:" bullet dump. Rendered to PDF with `fpdf2` and uploaded through the exact same real path a user upload goes through (`document_storage.validate_cv_upload`/`upload_cv`), marked with `original_filename == "golden_cv.pdf"` for idempotency.

Two real bugs surfaced and were fixed during the first live run, not assumed in advance:
- fpdf2's core "Helvetica" font is **latin-1 only** — LLM output routinely includes Unicode bullets/smart quotes/em-dashes, which crashed PDF rendering outright until sanitized (`_sanitize_for_core_font`).
- The LLM writes **Markdown** (`**bold**`, `# headings`) even though the render target is a plain-text PDF cell, not a Markdown renderer (`_strip_markdown`).

### Golden job postings (`app/golden/generate_jobs.py`)

Each candidate gets **three** independently LLM-written job postings, not one — a candidate whose matcher-returned top-3 contains 2-of-3 known positives is distinguishable from one with 0-of-3, which a single positive can't show. All three share the same *target* skill set (derived straight from the candidate's own `CandidateSkill` rows: `intermediate+` proficiency → `required`, `beginner` → `preferred`), but differ in company, title phrasing, and the LLM's independently-sampled prose — three genuinely different postings that should all still rank as strong matches, not three copies of one.

The explicit candidate→job mapping lives in a new `golden_pairs` table (`app/golden/models.py`), not encoded into a job's title/company as a string marker — a small, queryable table the recommendation-eval harness reads directly.

### Real Keycloak logins (`app/golden/provision_logins.py`)

Reuses `app/frontend/keycloak_admin.py`'s existing Admin-API client unchanged — the same one backing real self-registration, which already splits `full_name` into `firstName`/`lastName`. That turned out to matter: Keycloak 26's built-in `VERIFY_PROFILE` required action is enabled by default and resolves dynamically against the realm's User Profile schema regardless of any `defaultAction` flag, so a user created *without* those fields set (as this project's CI throwaway test user originally was, see `.github/workflows/ci.yml`'s history) fails every password-grant login with `invalid_grant: Account is not fully set up` even though every field the Admin API reads back looks complete. Reusing the production registration path sidesteps this entirely rather than re-deriving the fix.

Email/password are fully deterministic from `candidate_id` (`golden-candidate-{id}@example-synthetic.test` / a shared fixed dev password) — no credential manifest file is generated or stored, same spirit as the realm's existing checked-in dev-mode demo passwords.

### Persona-driven behavioral history (`app/golden/generate_persona_events.py`)

The generic event generator (`app/analytics/generate_synthetic_events.py`) assigns sessions uniformly at random across all 2000 candidates and always sets `received_at = now()`. That's a real problem for this dataset's purpose: `app/analytics/segmentation.py`'s recency feature is computed from `received_at` (the server-set, authoritative-for-ordering column), not the client-supplied `occurred_at` — so on the generic data, every candidate looks equally "just active" regardless of any assigned behavior, and the existing `GET /actors/candidate/{id}/segment` endpoint can never return anything but a volume-driven label.

Each of the 500 is assigned one of five persona archetypes, independently of occupation/domain (so skill-based and behavior-based structure aren't confounded), and the generator **deliberately backdates `received_at`**, grounded directly in `segmentation.py`'s real thresholds:

| Persona | Target rule-based segment | Pattern | Share |
|---|---|---|---|
| `power_applier` | `active_seeker` (via conversion ≥ 50%) | Frequent recent sessions, moderate shown-count, high click→apply conversion | 15% |
| `active_browser` | `active_seeker` (via volume ≥ 5 events/30d) | Frequent recent sessions, high shown/clicked volume, low conversion | 20% |
| `passive_seeker` | `passive_seeker` | One low-volume session within 30 days, rarely clicks, never applies | 30% |
| `dormant_recent` (churned) | `dormant` | A real burst of activity 45-90 days ago, then nothing since | 20% |
| `dormant_never_engaged` | `dormant` | Zero or one event total, 100+ days old | 15% |

A skewed, not uniform, distribution — deliberately, so a later business-insights narrative has something to say ("only 15% of candidates are highly engaged").

### Eval harnesses (`app/golden/run_extraction_eval.py`, `app/golden/run_recommendation_eval.py`)

- **Extraction**: reuses `app/extraction/run_extraction_eval.py`'s full persistence/metrics/MLflow pipeline unchanged via a small additive `examples` parameter — only the input dataset differs (golden CVs/jobs instead of generator-injected/hand-authored snippets). CVs are stored as PDFs, and this project has no existing CV-to-text pipeline (a documented, deliberately deferred gap) — this harness parses the real uploaded PDF bytes back to text via `pypdf` at eval time, so it genuinely exercises "real CV bytes in, skills out," not a cached-text shortcut.
- **Recommendation**: calls `app/matching/rank.py:rank_jobs_for_candidate` directly (not routed through `app/evaluation/run_evaluation.py`, simpler and more decoupled than threading a new parameter through the production harness for a need only this golden set has) and checks how many of each candidate's 3 `golden_pairs` jobs land in their real top-K — precision@3/recall@3 against known positives.
- **Second serving backend**: `--backend {ollama,vllm}` (`app/golden/vllm_client.py` alongside the existing `app/extraction/ollama_client.py`), added specifically so this same harness could compare model families/sizes across both a CPU-served Ollama path and a GPU-served vLLM path without duplicating the eval logic per backend — see `docs/architecture/application-services/skill-extraction.md`'s model-comparison results for what it found.
- **Targeted "hard case" re-evaluation** (`--hard-cases-f1-below <threshold> --compare-run-ids <ids>`): rather than re-running the full dataset (a 14-18+ hour CPU job per bigger model) to test whether a larger model actually helps, this queries `extraction_example_results` for the subset of examples where every one of the given prior runs already scored below the threshold, and evaluates only that subset against the new model. A general, reusable capability (`_hard_case_example_ids`/`_examples_by_ids` in `app/golden/run_extraction_eval.py`), not a one-off script, because "does a bigger/different model help on the cases that are actually hard" is a question worth asking again whenever a new model candidate shows up.

### Decomposition-tree report (`app/golden/build_evaluation_tree.py`)

A business-facing "where exactly is the score good or bad" view, requested directly: overall F1 drilling down into one of 4 profession domains (`SKILL_DOMAINS` in `generate_synthetic_data.py` — `software_engineering`, `business_ops`, `trades_logistics`, `people_services`) then into individual occupations, built separately for candidates (500 CVs) and job posts (1500 postings) so each branch's leaf counts sum to exactly those numbers. Grafana has no decomposition-tree/sunburst visual, so this renders a single self-contained Plotly Icicle chart to `data/golden/reports/evaluation_drilldown_run<id>.html` (plotly.js inlined — opens in any browser, no server, shareable as one file) — the open-source equivalent of Power BI's/Tableau's decomposition tree. Run via `docker compose run --rm golden-eval-tree [--run-id N]` (defaults to the best-scoring full-2000-sample run).

Two real data-quality issues were found and fixed while building this, not assumed away: (1) occupations are seeded by cycling a short list of real titles with a numeric suffix once the list runs out (`"Welder 4"`) — the first version of this report fragmented into ~170 near-duplicate 1-5-count leaves until titles were canonicalized back to the base profession; (2) each occupation's *domain* in the seed data (`occupation_skills`) is anchored **uniformly at random** per occupation row (`_assign_domain_anchored_skills` → `rng.choice(domain_names)`), with no semantic link to the title — it exists purely to give the skill co-occurrence graph (`docs/knowledge-graph.md`) real community structure, not to encode a real professional taxonomy. Confirmed live: before this was caught, "Welder" (and every other title with multiple padding rows) showed up scattered across multiple domains depending on which row got sampled — technically faithful to the seed data, but nonsensical to a business reader. Fixed with an explicit, human-judged `_CURATED_OCCUPATION_DOMAINS` map for the 26 real occupation titles (data-driven skill-category majority vote stays as the fallback for anything not in that list, e.g. user-added occupations).

One finding this surfaced immediately, worth carrying into any read of the golden-eval numbers elsewhere in this doc or in `skill-extraction.md`: candidate CVs extract substantially better than job postings in *every* domain (e.g. run 30: trades_logistics CVs 0.94 avg F1 vs. trades_logistics job posts 0.77) — not a fluke of one domain, a consistent gap across all 4.

### Backup & restore (`app/golden/export_backup.py`, `app/golden/restore_backup.py`)

The CVs and job postings are the only expensive-to-regenerate part of this dataset (~2,000 Ollama calls) — logins and behavioral events are fast/deterministic (Keycloak admin calls, seeded RNG) and cheaper to just re-run than to back up. `golden-export` downloads all 500 CV PDFs from object storage and dumps every golden job's full `Job`/`JobSkill`/`GoldenPair` data to `data/golden/backup/` (git-ignored, a local recovery copy, not checked into version control). `golden-restore` reads it back, idempotently (same "already present" markers as the generators), re-uploading CVs and re-inserting jobs without calling Ollama at all — recovers a wiped Docker volume in seconds instead of hours. Logins/events still need `golden-logins`/`golden-events` run afterward (fast, not part of the backup).

## Verification & Results

- All 8 scripts smoke-tested with `--limit N` against the real local stack before any full run: CV generation (inspected actual rendered PDF content by eye — realistic, natural prose, not a template echo), job generation (3 independently-written postings per candidate, same skill ground truth, verified via direct DB query), login provisioning (confirmed an actual password-grant token exchange succeeds against the newly created account), persona events (verified against the *real* `segment_actor()` function, not assumed: 0/100 mismatches between assigned persona and returned segment on a 100-candidate sample, after two real bugs in the passive-seeker volume tuning were found and fixed this way), extraction eval (real, non-trivial precision/recall numbers — not all-zero or all-perfect, which would indicate a bug rather than a real signal), recommendation eval (recall@10 = 1.0 and precision@10 = 0.3 for the one fully-generated test candidate — all 3 golden jobs found).
- A real transient Ollama timeout crashed the first attempt at the full 500-candidate CV batch on the very first candidate (confirmed live, not theoretical) — fixed with a small retry wrapper (`app/golden/llm_retry.py`) and per-candidate exception handling in both `generate_cvs.py` and `generate_jobs.py`, so one candidate's persistent failure no longer aborts a multi-hour batch; a future re-run simply tops up whatever's still missing.
- Unit tests: `tests/golden/test_select.py` (determinism, occupation spread, exclusion rules — DB-backed), `tests/golden/test_generate_persona_events.py` (persona-assignment determinism and distribution), `tests/golden/test_text_cleanup.py` (the two real rendering bugs above, regression-guarded).

## Known Limitations

- LLM generation cost is real: ~2000 total Ollama inferences for the full 500×(1 CV + 3 jobs) batch, each 8-30s warm — expect hours of wall-clock time for a full run, mitigated by resumability (every script skips work already done) rather than by making it fast.
- The persona→segment mapping is probabilistic, not guaranteed, by the nature of a rule-based threshold applied to randomly-sampled event counts/timing — verified at 0/100 mismatches on a sample, not asserted as a hard guarantee for every one of the 500.
- No live per-request traffic simulator yet — the persona events are bulk-inserted with backdated timestamps (fast, controls the statistical distribution precisely), not generated by hitting the real authenticated API end-to-end. A smaller, complementary script using the 500 real logins from this dataset is a natural follow-up for demonstrating the live pipeline, not how the bulk historical volume here was built.
- The actual k-means/clustering analysis this dataset exists to support is not part of this build.

## Roadmap

- K-means (or HDBSCAN) clustering over KPIs this dataset makes possible (login frequency, click-through/apply rates, recency, skill breadth, occupation/domain) — `app/analytics/segmentation.py`'s own "v3" note.
- A live traffic simulator using the 500 real golden logins, to demonstrate the full authenticated request path (not just bulk-inserted history) producing behavioral events in near-real-time on the "JME Product Analytics" Grafana dashboard.
- Extending `golden_pairs` with graded "distractor" jobs (partial, not near-perfect, skill overlap) if finer-grained ranking evaluation is ever needed beyond precision@3/recall@3.

## Related Documents

- [application-services/behavioral-analytics.md](application-services/behavioral-analytics.md) — the engagement segmentation logic this dataset is built to exercise
- [application-services/skill-extraction.md](application-services/skill-extraction.md) — the extraction pipeline and its existing (easier) golden dataset
- [ingress-and-identity.md](ingress-and-identity.md) — the JWT/ownership model this dataset's real Keycloak logins exercise
