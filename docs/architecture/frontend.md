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

**Reuses the existing seeded dataset for job records, no data-entry UI for them**: an employer
enters an existing `job_id`. Building a UI to create *new* job records would mean a real write-path
into `app/ingestion/source_models.py`'s tables, deliberately not built for a UI whose whole point is
demonstrating the matching flow, not demonstrating data entry (see Roadmap). The Candidate side no
longer picks from the seeded dataset at all (issue #17): registering as a Candidate creates a real,
brand-new `candidates` row for that person (see Implementation), so there's no `candidate_id` input
to show — each candidate's CV/matches are already their own.

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

## Implementation

`app/frontend/streamlit_app.py`  one script, two modes selected by the user's real
`candidate`/`employer` role, sourced from the `user_profiles` table (`_require_profile()`, see
[ingress-and-identity.md](ingress-and-identity.md) Part 2) rather than an unauthenticated UI toggle:
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
  state.
- **Employer**: enter a `job_id` → click "Get Matching Candidates" (`GET /jobs/{id}/candidate-matches`
  on `api`) → same results table.

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

## Known Limitations

- Authentication exists (Keycloak SSO, see [ingress-and-identity.md](ingress-and-identity.md)), but authorization only partly does now: each candidate has their own real `candidate_id` (issue #17), so one candidate can no longer see or overwrite another's CV/matches — but an employer can still type in any `job_id` in the seeded dataset, nothing ties a specific Keycloak identity to a specific `job_id` yet. Acceptable for a local demo, not for anything deployed (same class of gap as assumption A9).
- The `candidates` row created at Candidate registration (issue #17) still starts with placeholder `location_city`/`location_region` (`"Unknown"`) and `null` salary/occupation/skills — but issue #18's "Your profile" form now lets the candidate fill in real values themselves post-registration, closing most of that gap (years_experience remains uncollected by any UI).
- Skills set via "Your profile" (issue #18) are always recorded as `proficiency=intermediate`/`evidence_source=self_reported` — there's no UI for a candidate to claim a different proficiency level, a simplification since CV-extraction-sourced skills (the other `evidence_source`) aren't chained in yet either (see below).
- Saving "Your profile" does not invalidate the `GET /candidates/{id}/job-matches` Redis cache (`docs/redis-infrastructure.md`'s 1-hour TTL) — a candidate who saves new details and immediately re-checks matches within that hour can see a stale, pre-save score until the cache entry expires. Acceptable for a demo; a real system would bust the cache key on profile save.
- No candidate/job *record* creation beyond what issue #18 added (editing is now possible for a candidate's own scoring fields; creating new candidate/job rows from the UI, or any employer-side self-service, is still out of scope for v1).
- CV upload and skill extraction are still two separate, un-chained steps (see `docs/architecture/application-services/skill-extraction.md`'s own Known Limitations), uploading a CV here does not automatically extract skills from it or affect the candidate's match scores.
- `streamlit.testing.v1.AppTest` has no built-in way to simulate an actual file upload, so the "upload succeeds and shows a success message" path is verified only against the live stack, not as an automated unit test.

## Roadmap

- **v2, only if actually useful for the portfolio demo**: parse the uploaded CV's text and run it through `extract_skills` automatically (the same gap `docs/architecture/application-services/skill-extraction.md` already flags), surfacing extracted skills in the UI.
- Simple data registration (create a new candidate/job *record* from the UI instead of picking an existing `candidate_id`/`job_id`), would need a real write-path into `app/ingestion/source_models.py`'s tables, not built yet. (Not to be confused with *user* registration, which is done issue #14, see [ingress-and-identity.md](ingress-and-identity.md).)

## Resource Footprint and Scaling

Streamlit holds one live session per browser connection in the `frontend` process's own memory (`st.session_state`, including the SSO login state, `docs/architecture/ingress-and-identity.md`), so the real concurrent-user ceiling for the demo UI itself is however many sessions one Streamlit process can hold before memory/CPU on that single container becomes the constraint, not measured here (this is a single-developer demo UI, not the system's actual capacity bottleneck; see `docs/operations/capacity-planning.md`). Streamlit does not horizontally scale behind a load balancer the way a stateless API does without sticky sessions, since a session lives in one process, the two Reverse Proxy replicas in front of it already round-robin, but a given browser's session only works correctly if routed consistently (today it works because there's exactly one `frontend` container; adding a second would need session affinity, not built here). The UI is a thin client either way, its own footprint is not where this platform's real capacity limits are; see `docs/operations/capacity-planning.md` for the full worked scenario.

## Related Documents

- `docs/architecture/application-services/skill-extraction.md` : the CV upload endpoint this UI calls
- `docs/architecture/application-services/matching-engine.md` : the job matches/candidate-matches endpoints this UI calls
- `docs/architecture.md` : overall system architecture
- `docs/progress.md` : full build history
