# Demo Frontend

## Purpose & Scope

Every prior phase built a real capability behind an API, but nothing in this project let a human
actually click through the flow it's built for the platform was API-only. This phase closes that
one gap with a deliberately minimal web UI: a candidate can upload a CV and see their job matches;
an employer can see matching candidates for a job. Nothing else. Complicated functionality (editing
a profile, posting a new job, creating new candidate/job *records*) is explicitly deferred, see
Roadmap. Identity registration (signing up a new *user*, as opposed to creating a new candidate/job
record) is a separate concern, added later via Keycloak self-registration see
[ingress-and-identity.md](ingress-and-identity.md) Part 2.

**Out of scope, by design**: candidate/job *record* creation, any new backend logic. This is
a UI layer over APIs that already exist, not a new feature. (Authentication was originally out of
scope too; a real OIDC login gate and self-registration were added later, see
[ingress-and-identity.md](ingress-and-identity.md).)

## Architecture & Design

**Streamlit, not a JS framework**: for a demo UI whose entire job is "call an API, show the JSON
as a table," a Python-only framework that renders from a single script is the right-sized tool,
no separate frontend build pipeline, no new language in the stack. The tradeoff (less control over
styling/UX than React/Vue) doesn't matter here, since polish was explicitly deferred.

**A separate image, not bolted onto `api`**: consistent with every other service boundary in this
project (`api`/`analytics`/`extraction` are each independently deployable). The frontend never
touches Postgres directly, it only calls `api` and `extraction` over HTTP, the same way a real
external client would. This is also the smallest image in the project: no `app/db.py`, no ORM, no
`requirements-base.txt` at all.

**Neither side picks from the seeded dataset anymore.** The Candidate side stopped first (issue
#17): registering as a Candidate creates a real, brand-new `candidates` row for that person (see
Implementation), so there's no `candidate_id` input to show — each candidate's CV/matches are
already their own. Issue #22 closed the same gap on the Employer side: an employer now creates and
owns real `jobs` rows (`Job.posted_by_keycloak_user_id`) instead of typing a raw `job_id` from the
1,000-row seeded dataset — that dataset still exists and is still what candidates get ranked
against/from, it's just no longer the *only* source of job records a real logged-in user can act on.

This is distinct from *user* identity, which issue #14 did add a minimal real model for: Keycloak
self-registration plus a `candidate`/`employer` realm role, assigned via a one-time in-app picker
on first login. See [ingress-and-identity.md](ingress-and-identity.md) Part 2 for that flow; it's
about who the user is, not what dataset they operate on.

Issue #15 added real backend storage for that identity: a `user_profiles` table (name, role,
contact info, linked by the Keycloak `sub` claim) plus a `POST`/`GET /profiles` API. Issue #16
replaced the one-time role-picker buttons with a profile form (name, a candidate/employer radio
button, company name for employers) that calls that API directly at registration time, and gave
the logged-out screen explicit "Log in" / "New here?" section headings. Issue #17 then closed the
remaining gap between that identity and the matching-engine dataset: `POST /profiles` now also
creates a personal `candidates` row for role=candidate and links its id onto the profile, so a
candidate's CV/matches are tied to their own record — not one shared hardcoded id. See
[ingress-and-identity.md](ingress-and-identity.md) Part 2.

Issue #18 closed the remaining gap between that per-candidate record and the matching engine's
actual scoring inputs: `app/matching/scoring.py` has always scored skills (55%), location (15%),
salary (15%), and occupation (15%), but nothing let a candidate set those values — they stayed at
issue #17's registration-time placeholders forever. A new "Your profile" section (between
"Upload your CV" and "Show Job Matches") lets a candidate edit all four directly, backed by new
`GET`/`PATCH /candidates/{id}/details` and `GET /occupations`/`GET /skills` endpoints (both small
lookup tables, loaded in full for a selectbox/multiselect — no search/pagination needed at this
scale).

Issue #19: the seeded dataset pads its 150 `Occupation` rows out of only 26 real titles by
appending a number ("Accountant 2", "Accountant 3", ...), which showed up verbatim in the
occupation picker. `Occupation.is_curated` (backfilled by migration — the padding rows match
`title ~ '\s\d+$'`) lets `GET /occupations` filter those out, and new `POST /occupations`/
`POST /skills` endpoints (case-insensitive get-or-create) let a candidate add one that isn't
listed — a "+ Add a new occupation" sentinel option and a free-text "Add a new skill" input in
the frontend call these before saving.

Issue #20: location/salary/experience are collected for later filtering/analysis, not for
scoring — the user explicitly wants scoring kept to skills + education only (see Roadmap; that
redesign is tracked separately, issue #21, not done yet). The location row became City/Postal
code/Country (`Candidate.postal_code`/`.country`, two new nullable columns) — `location_region`
(still the field `_location_component` actually scores on) is no longer collected from the user at
all; `CandidateDetailsUpdate` omits it on purpose, so a PATCH leaves whatever value is already on
the row untouched rather than overwriting it with nothing. A 4-bucket experience selector (Entry
Level/Mid-Level/Senior/Expert) replaced a raw years-of-experience number, mapped to/from the
existing `years_experience` column server-side (`_years_experience_to_level`/
`_EXPERIENCE_LEVEL_TO_YEARS` in `profile_routes.py`) — no new column, since nothing besides
display reads this value as anything but an int.

## Implementation

`app/frontend/streamlit_app.py`  one script, two modes selected by the user's real
`candidate`/`employer` role, sourced from the `user_profiles` table (`_require_profile()`, see
[ingress-and-identity.md](ingress-and-identity.md) Part 2) rather than an unauthenticated UI toggle:

A "Contact Us" sidebar button (next to "Log out", both roles) toggles a static page with a
`mailto:` link — `st.session_state["show_contact"]`, same `st.stop()`-gated toggle pattern as the
login/registration screen switch. No backend involved; just a static support-contact page, same
spirit as a portfolio project's "about/contact" footer.
- **Candidate**: `candidate_id` comes from `st.session_state["profile"]["candidate_id"]` — the
  real, personal `candidates` row created for this user at registration (issue #17, see
  [ingress-and-identity.md](ingress-and-identity.md) Part 2). "Upload your CV" first calls
  `GET /candidates/{id}/cv` on `extraction` — if a CV is
  already on file, its filename/size/upload date are shown and the uploader is labeled "Replace
  your CV"; otherwise it's a plain upload prompt. Uploading (`POST /candidates/{id}/cv`, client-side
  size check mirrors the server's 2MB cap before even sending the request) re-renders the page so
  the "current CV on file" info reflects the new upload. "Show Job Matches"
  (`GET /candidates/{id}/job-matches?top_k=10` on `api`) renders results as a table (label,
  eligibility, score, explanation), with the `X-Ranking-Mode` response header surfaced so it's
  visible whether blended or rule-based ranking was used.
  "Your profile" (issue #18, `app/frontend/candidate_client.py`) then lets them edit the four
  fields `scoring.py` actually reads: a multiselect for skills (capped at 10, options loaded from
  `GET /skills`), city/region text inputs, salary min/max number inputs, and an occupation
  selectbox (options from `GET /occupations`). "Save profile details" calls
  `PATCH /candidates/{id}/details`, which replaces that candidate's skill rows wholesale and
  updates the `Candidate` columns directly, then reruns the page so the form reflects the saved
  state. An "Edit history" expander (issue #24, `app/frontend/audit_client.py`) shows the last few
  changes from `GET /audit/candidate_details/{id}`.

  Issue #24 also added real "Apply"/"Save for later" actions, and issue #25 gave them a proper
  home: after "Show Job Matches," the results are stashed in `st.session_state["last_job_matches"]`
  (so they survive reruns), and a picker below the table lets the candidate choose one and click
  "View Job Details" — this loads a full drill-down view (`_render_job_detail_view`): the job's
  title/description/company/location, salary, resolved occupation title (`JobDetailsOut.
  occupation_title`, resolved server-side since `GET /occupations` only returns curated titles but
  a matched job can have any occupation), required/preferred skills, with "Apply"/"Save for
  later"/"Back to results" buttons right there — not at the picker level. Opening this view itself
  calls `GET /jobs/{id}/details?viewed_by_candidate_id={id}`, which fires `recommendation_clicked`
  (the one event type issue #24 left unemitted — the same endpoint's employer edit-form load omits
  the param, so it doesn't mis-fire there). "Apply"/"Save" call
  `POST /candidates/{id}/jobs/{job_id}/apply`/`.../save`. An "Your applied/saved jobs" expander on
  the main results page lists the current state from `GET /candidates/{id}/job-interactions`.
- **Employer** (issue #22, `app/frontend/job_client.py`): a "Your jobs" selectbox lists that
  employer's own postings (`GET /employers/{keycloak_user_id}/jobs`) plus a "+ Post a new job"
  option — no more raw `job_id` input. Picking an existing job loads its full detail
  (`GET /jobs/{id}/details`) into an edit form below: title, description, company name,
  City/Postal code/Country (issue #23, same pattern as the candidate side's issue #20 —
  `location_region` stays on the `Job` row, still read by `_location_component`, but is no longer
  collected from the user), salary min/max, occupation (+ add-new), required/preferred skill
  multiselects (+ add-new skill) and a minimum-experience bucket, mirroring the candidate profile
  form. "Save job" calls `POST /jobs` (new) or `PATCH /jobs/{id}/details` (existing), replacing
  that job's `JobSkill` rows split by `requirement_level` — required/preferred skills plug
  directly into the existing 70/30 split `scoring.py`'s `_skill_component` already uses, no
  scoring changes needed. A skill picked as both required and preferred is deduped server-side
  (required wins) rather than violating `job_skills`' unique constraint. "Get Matching Candidates"
  (`GET /jobs/{id}/candidate-matches`, unchanged, already `top_k=10` by default) operates on
  whichever job is selected. Every job-form widget's key is suffixed with the selected job's id
  (or `"new"`) — Streamlit only honors a widget's `value=` the first time that exact key renders
  in a session, so a fixed key would otherwise let a previous job's cached field value silently
  carry into a later job's save on switching (see Verification & Results for the bug this fixed).
  An "Edit history" expander (issue #24) shows that job's own change history from
  `GET /audit/job_details/{id}`.

`docker/Dockerfile.frontend` / `app/requirements-frontend.txt` just `streamlit` and `requests`,
nothing else. `docker-compose.yml`'s `frontend` service depends on `api` and `extraction` being
healthy, exposes `8501` (Streamlit's default), and is configured via `API_URL`/`EXTRACTION_URL`/
`JME_API_KEY` env vars, the same `X-API-Key` header every other caller in this project uses,
attached server-side so it's never exposed to the browser.

**Access via the ingress layer, not `:8501` directly**: `API_URL`/`EXTRACTION_URL` now point at the API Gateway (`http://gateway/matching`,
`http://gateway/extraction`), and the demo itself is reached at `http://localhost:8080` (the Load
Balancer), not `http://localhost:8501`. `:8501` is still published for direct debugging, same
convention as every other service, but the real path is `candidate -> Load Balancer -> Reverse Proxy -> frontend`. See `docs/architecture/ingress-and-identity.md`.

## Verification & Results

**Tested with Streamlit's own official test framework** (`streamlit.testing.v1.AppTest`), not a
browser automation tool, runs the real script headlessly and simulates widget interactions (role switch, button clicks, text input) with HTTP calls mocked. 7 tests: app renders without error, Candidate is the default role, switching to Employer shows the right widgets, a successful job-matches call renders a results table with the correct endpoint called, a failed call surfaces the real error text, an empty file upload leaves the upload button disabled (no accidental empty POST), and the employer flow calls the correct candidate-matches endpoint.

**Verified live against the real stack, not just the mocked unit tests**: built the `frontend` image, started it alongside real `api`/`extraction`/`db`/`redis`/`seaweedfs` containers, and replicated the exact three HTTP calls the app makes:
- `POST /candidates/3/cv` (CV upload) → `201`, file landed in both Postgres metadata and object storage (same verification as `docs/architecture/application-services/skill-extraction.md`'s own CV upload check).
- `GET /candidates/3/job-matches` → `200`, 10 ranked results, `X-Ranking-Mode: rule_based_no_embeddings`.
- `GET /jobs/5/candidate-matches` → `200`, 10 ranked results.

Also confirmed the frontend container reaches `api` over the **internal Docker network** (not just the host-mapped port) by running the exact `requests.get` call from inside the running `jme-frontend-1` container itself, the way it will actually be called in practice, not assumed from the host-side test.

**MVP simplification round** (removed the `candidate_id` input, added the existing-CV check, renamed the job-matches button): verified live against the running stack — `GET /candidates/{id}/cv` returns `404` before any upload (so the plain upload prompt renders, not the "current CV on file" info box) and `GET /candidates/{id}/job-matches?top_k=10` returns real ranked results. `tests/frontend/test_streamlit_app.py` covers both the existing-CV and no-existing-CV render paths headlessly (mocked `requests.get`).

**Issue #17** (real per-user `candidate_id`, not the hardcoded `1` above): verified live against the running stack with a scripted session — registered two distinct brand-new Candidates and confirmed each got their own, different `candidate_id` (not the seeded dataset's `candidate_id=1` at all, real new rows); uploaded a CV to the first candidate's own id and confirmed the second candidate's `GET /candidates/{id}/cv` still returned `404` not the first candidate's CV; confirmed an Employer registration left `candidate_id` `null`. Test rows cleaned up after. `tests/api/test_profile_routes.py` covers the linking/reuse logic against a real (disposable) Postgres transaction.

**CI**: `fresh-volume-smoke-test` builds and Trivy-scans the `frontend` image, starts it after `api`/`extraction` are healthy, confirms `/_stcore/health` and the main page both return `200`, and runs the same internal-network check inside the container.

**Issue #18** (editable location/salary/occupation/skills): verified live against the running
stack — `PATCH /candidates/{id}/details` with real values persisted to `candidates` and
`candidate_skills`, confirmed via `docker compose exec db psql`; re-ran
`GET /candidates/{id}/job-matches` before and after and confirmed the score breakdown's
location/salary/occupation components moved off their neutral `0.5` defaults once real data was
set. `tests/api/test_candidate_details_routes.py` covers the GET/PATCH/occupations/skills routes
against a real (disposable) Postgres transaction; `tests/frontend/test_streamlit_app.py` covers
the form rendering pre-filled values and the save button calling `PATCH` with the expected payload.

**Issue #19** (curated occupations, add-new-occupation/skill): verified live — `GET /occupations`
returns exactly the 26 real titles (confirmed via `psql`, the backfill correctly flagged
"Accountant 2".."Accountant 6" etc. as `is_curated=false`); `POST /occupations`/`POST /skills`
create new rows and are idempotent on a case-insensitive re-post (verified with `curl`); linked a
newly created occupation + skill onto a real candidate via `PATCH /candidates/{id}/details`.

**Issue #20** (postal code/city/country, experience bucket): verified live — saved real
postal_code/country/experience_level on the session's real candidate via `curl`, confirmed via
`psql` that `location_region` (set in an earlier session) was untouched by the PATCH while
`postal_code`/`country`/`years_experience` persisted, and that `GET /candidates/{id}/details`
round-trips the experience bucket ("senior" in → "senior" back out, mapped through
`years_experience=8`). `tests/api/test_candidate_details_routes.py` covers the bucket mapping at
each boundary (0, 2, 3, 5, 6, 10, 11 years) and confirms a PATCH that omits `location_region`
never overwrites it.

**Issue #22** (employer-owned job postings): verified live — created a real job via the employer
account created this session (`ai-tech@gmail.com`) with real required/preferred skills, salary,
location, occupation, and experience; confirmed via `psql` that `Job.posted_by_keycloak_user_id`
and the split `JobSkill` rows (`required`/`preferred`) landed correctly; confirmed the job appears
in `GET /employers/{id}/jobs`; confirmed `GET /jobs/{id}/candidate-matches` returns real ranked
results (10, non-neutral scores) unchanged from before. `tests/api/test_job_routes.py` covers
create/list/get/patch and the skill-count cap; `tests/frontend/test_streamlit_app.py` covers the
job-picker rendering, posting a new job, and the existing "Get Matching Candidates" flow still
working against a selected job.

**Issue #23** (job location: city/postal code/country, overlapping-skill fix): verified live
against the real job created in issue #22's verification — PATCHed real postal code/country onto
it and confirmed via `psql` that `location_region` (set at creation) was untouched while
`postal_code`/`country` persisted; also reproduced and fixed the original crash where picking the
same skill as both required and preferred violated `job_skills`' unique constraint (required now
wins silently). `tests/api/test_job_routes.py` covers the postal/country round-trip, the
`location_region` preservation, the "Unknown" placeholder default for a brand-new job, and the
overlapping-skill dedup.

**Stale-widget-key bug fix** (reported live: editing a posted job and saving, then logging back
in, showed different values than what was set): root-caused to every job-form widget sharing a
fixed key regardless of which job was selected — Streamlit only applies a widget's `value=`
argument the first time that key renders in a session, so switching from "+ Post a new job" (or
another job) to a given job did not actually refresh the displayed fields to that job's real data;
saving in that state silently overwrote the job with stale/blank values from whichever job (or the
blank new-job form) had been viewed previously in the same session. Fixed by suffixing every
job-form widget's key with the selected job's id (or `"new"`), forcing Streamlit to treat each
job's fields as distinct widgets. `tests/frontend/test_streamlit_app.py`'s
`test_switching_between_jobs_refreshes_form_fields_not_stale_values` reproduces the exact
switching sequence and asserts the field refreshes correctly both directions.

**Issue #24** (missing behavioral events, Apply/Save, edit-history audit trail): verified live —
applied to and saved a real job as the real candidate from this session, confirmed both
`job_applied`/`job_saved` analytics events and the underlying `candidate_job_interactions` row
(see `docs/architecture/application-services/behavioral-analytics.md` for the event-logging
verification); PATCHed the candidate's and the job's details with a real
`changed_by_keycloak_user_id` and confirmed `GET /audit/candidate_details/{id}` and
`GET /audit/job_details/{id}` each returned a diff of only the fields that actually changed.
`tests/api/test_audit_routes.py` covers `record_change`'s diff-only behavior (a no-op update
writes nothing) and history ordering; `tests/api/test_job_routes.py` covers apply/save idempotency
and the audit entries create_job/update_job_details now write; `tests/frontend/
test_streamlit_app.py` covers the Apply/Save buttons appearing after a job-matches result and the
edit-history expander rendering.

**Issue #25** (clickable job matches, real job-details view, `recommendation_clicked`): verified
live — called `GET /jobs/{id}/details?viewed_by_candidate_id={id}` as the real candidate from this
session and confirmed a `recommendation_clicked` event via `GET /actors/candidate/{id}/events`;
confirmed `occupation_title` resolves correctly even for a job whose occupation isn't in the
curated list; confirmed the same endpoint called without the query param (the employer's own
edit-form load path) does not fire a second click event. `tests/api/test_job_routes.py` covers
`occupation_title` resolution and that `recommendation_clicked` only fires when
`viewed_by_candidate_id` is present; `tests/frontend/test_streamlit_app.py`'s
`test_viewing_a_match_shows_details_with_apply_save_and_back` covers the full
view-details/apply/back sequence.

## Known Limitations

- Authentication exists (Keycloak SSO, see [ingress-and-identity.md](ingress-and-identity.md)), but authorization only partly does now: each candidate has their own real `candidate_id` (issue #17) and each job is tied to the employer who posted it (`Job.posted_by_keycloak_user_id`, issue #22) — but `GET /jobs/{id}/details`/`PATCH /jobs/{id}/details` don't actually check that the caller owns the job before returning/editing it (same no-JWT-verification trust boundary as every other route here, not enforced authorization), and an employer can still type a seeded dataset's `job_id` directly against the API (not the UI) to view/edit a job they didn't post. Acceptable for a local demo, not for anything deployed (same class of gap as assumption A9).
- The `candidates` row created at Candidate registration (issue #17) still starts with placeholder `location_city`/`location_region` (`"Unknown"`) and `null` salary/occupation/skills — but issue #18's "Your profile" form now lets the candidate fill in real values themselves post-registration, closing most of that gap (years_experience remains uncollected by any UI).
- Skills set via "Your profile" (issue #18) are always recorded as `proficiency=intermediate`/`evidence_source=self_reported` — there's no UI for a candidate to claim a different proficiency level, a simplification since CV-extraction-sourced skills (the other `evidence_source`) aren't chained in yet either (see below).
- Saving "Your profile" does not invalidate the `GET /candidates/{id}/job-matches` Redis cache (`docs/redis-infrastructure.md`'s 1-hour TTL) — a candidate who saves new details and immediately re-checks matches within that hour can see a stale, pre-save score until the cache entry expires. Acceptable for a demo; a real system would bust the cache key on profile save.
- Job skills set via "Your jobs" (issue #22) have no equivalent of the candidate side's `proficiency`/`evidence_source` nuance — `JobSkill.requirement_level` (required/preferred) is the only distinction captured, consistent with what the form actually asks for.
- CV upload and skill extraction are still two separate, un-chained steps (see `docs/architecture/application-services/skill-extraction.md`'s own Known Limitations), uploading a CV here does not automatically extract skills from it or affect the candidate's match scores.
- `streamlit.testing.v1.AppTest` has no built-in way to simulate an actual file upload, so the "upload succeeds and shows a success message" path is verified only against the live stack, not as an automated unit test.
- `GET /audit/{entity_type}/{entity_id}` (issue #24) has no ownership check either, same trust-boundary limitation as the job-details routes above — anyone with the API key can read any entity's edit history, not just their own.
- All 6 taxonomy event types are now real (issue #25 closed the last gap, `recommendation_clicked`, via a candidate opening a specific match's details).

## Roadmap

- **v2, only if actually useful for the portfolio demo**: parse the uploaded CV's text and run it through `extract_skills` automatically (the same gap `docs/architecture/application-services/skill-extraction.md` already flags), surfacing extracted skills in the UI.
- Simple data registration (create a new candidate/job *record* from the UI instead of picking an existing `candidate_id`/`job_id`) is now done on both sides (issue #17 for candidates, issue #22 for jobs). (Not to be confused with *user* registration, which is done issue #14, see [ingress-and-identity.md](ingress-and-identity.md).)
- **Issue #21**: simplify matching to score on skills + education only (location/salary/experience become filter/analysis-only data, per the user's explicit direction while scoping issue #20) — needs a new "education" data concept (nothing in the schema represents it today) and a re-evaluation of the embeddings-blended ranker's documented NDCG numbers (`docs/embedding-matching.md`), which are keyed to the current rule-based weights. Not started.

## Resource Footprint and Scaling

Streamlit holds one live session per browser connection in the `frontend` process's own memory (`st.session_state`, including the SSO login state, `docs/architecture/ingress-and-identity.md`), so the real concurrent-user ceiling for the demo UI itself is however many sessions one Streamlit process can hold before memory/CPU on that single container becomes the constraint, not measured here (this is a single-developer demo UI, not the system's actual capacity bottleneck; see `docs/operations/capacity-planning.md`). Streamlit does not horizontally scale behind a load balancer the way a stateless API does without sticky sessions, since a session lives in one process, the two Reverse Proxy replicas in front of it already round-robin, but a given browser's session only works correctly if routed consistently (today it works because there's exactly one `frontend` container; adding a second would need session affinity, not built here). The UI is a thin client either way, its own footprint is not where this platform's real capacity limits are; see `docs/operations/capacity-planning.md` for the full worked scenario.

## Related Documents

- `docs/architecture/application-services/skill-extraction.md` : the CV upload endpoint this UI calls
- `docs/architecture/application-services/matching-engine.md` : the job matches/candidate-matches endpoints this UI calls
- `docs/architecture.md` : overall system architecture
- `docs/progress.md` : full build history
