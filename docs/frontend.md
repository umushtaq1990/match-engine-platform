# Demo Frontend

Status: v1 — implemented and verified end-to-end against the real running stack.

## Purpose & Scope

Every prior phase built a real capability behind an API, but nothing in this project let a human
actually click through the flow it's built for — the platform was API-only. This phase closes that
one gap with a deliberately minimal web UI: a candidate can upload a CV and see their job matches;
an employer can see matching candidates for a job. Nothing else. Complicated functionality (real
registration, editing a profile, posting a new job) is explicitly deferred — see Roadmap.

**Out of scope, by design**: authentication, candidate/job creation, any new backend logic. This is
a UI layer over APIs that already exist, not a new feature.

## Architecture & Design

**Streamlit, not a JS framework**: for a demo UI whose entire job is "call an API, show the JSON
as a table," a Python-only framework that renders from a single script is the right-sized tool —
no separate frontend build pipeline, no new language in the stack. The tradeoff (less control over
styling/UX than React/Vue) doesn't matter here, since polish was explicitly deferred.

**A separate image, not bolted onto `api`**: consistent with every other service boundary in this
project (`api`/`analytics`/`extraction` are each independently deployable). The frontend never
touches Postgres directly — it only calls `api` and `extraction` over HTTP, the same way a real
external client would. This is also the smallest image in the project: no `app/db.py`, no ORM, no
`requirements-base.txt` at all.

**Reuses the existing seeded dataset, no registration**: a candidate enters an existing
`candidate_id`, an employer enters an existing `job_id`. Building real account creation would mean
inventing a user model this project doesn't otherwise need (same reasoning as assumption A9's
single static API key) — deliberately not built for a UI whose whole point is demonstrating the
matching flow, not demonstrating auth.

## Implementation

`app/frontend/streamlit_app.py` — one script, two modes via a sidebar radio (`Candidate` /
`Employer`):
- **Candidate**: enter a `candidate_id` → upload a CV (`POST /candidates/{id}/cv` on `extraction`,
  client-side size check mirrors the server's 2MB cap before even sending the request) → click "Get
  My Job Matches" (`GET /candidates/{id}/job-matches` on `api`) → results rendered as a table
  (label, eligibility, score, explanation), with the `X-Ranking-Mode` response header surfaced so
  it's visible whether blended or rule-based ranking was used.
- **Employer**: enter a `job_id` → click "Get Matching Candidates" (`GET /jobs/{id}/candidate-matches`
  on `api`) → same results table.

`docker/Dockerfile.frontend` / `app/requirements-frontend.txt` — just `streamlit` and `requests`,
nothing else. `docker-compose.yml`'s `frontend` service depends on `api` and `extraction` being
healthy, exposes `8501` (Streamlit's default), and is configured via `API_URL`/`EXTRACTION_URL`/
`JME_API_KEY` env vars — the same `X-API-Key` header every other caller in this project uses,
attached server-side so it's never exposed to the browser.

## Verification & Results

**Tested with Streamlit's own official test framework** (`streamlit.testing.v1.AppTest`), not a
browser automation tool — runs the real script headlessly and simulates widget interactions
(role switch, button clicks, text input) with HTTP calls mocked. 7 tests: app renders without
error, Candidate is the default role, switching to Employer shows the right widgets, a successful
job-matches call renders a results table with the correct endpoint called, a failed call surfaces
the real error text, an empty file upload leaves the upload button disabled (no accidental empty
POST), and the employer flow calls the correct candidate-matches endpoint.

**Verified live against the real stack, not just the mocked unit tests**: built the `frontend`
image, started it alongside real `api`/`extraction`/`db`/`redis`/`seaweedfs` containers, and
replicated the exact three HTTP calls the app makes:
- `POST /candidates/3/cv` (CV upload) → `201`, file landed in both Postgres metadata and object
  storage (same verification as `docs/skill-extraction.md`'s own CV upload check).
- `GET /candidates/3/job-matches` → `200`, 10 ranked results, `X-Ranking-Mode: rule_based_no_embeddings`.
- `GET /jobs/5/candidate-matches` → `200`, 10 ranked results.

Also confirmed the frontend container reaches `api` over the **internal Docker network** (not just
the host-mapped port) by running the exact `requests.get` call from inside the running
`jme-frontend-1` container itself — the way it will actually be called in practice, not assumed from
the host-side test.

**CI**: `fresh-volume-smoke-test` builds and Trivy-scans the `frontend` image, starts it after
`api`/`extraction` are healthy, confirms `/_stcore/health` and the main page both return `200`, and
runs the same internal-network check inside the container.

## Known Limitations

- No authentication — anyone who can reach the frontend can look up any `candidate_id`/`job_id` in
  the seeded dataset. Acceptable for a local demo, not for anything deployed (same class of gap as
  assumption A9).
- No candidate/job creation or editing — only the two read-heavy flows (upload CV, view matches)
  exist. A real portal would need profile management; deliberately out of scope for v1.
- CV upload and skill extraction are still two separate, un-chained steps (see
  `docs/skill-extraction.md`'s own Known Limitations) — uploading a CV here does not automatically
  extract skills from it or affect the candidate's match scores.
- `streamlit.testing.v1.AppTest` has no built-in way to simulate an actual file upload, so the
  "upload succeeds and shows a success message" path is verified only against the live stack, not
  as an automated unit test.

## Roadmap

- **v2, only if actually useful for the portfolio demo**: parse the uploaded CV's text and run it
  through `extract_skills` automatically (the same gap `docs/skill-extraction.md` already flags),
  surfacing extracted skills in the UI.
- Simple registration (create a new candidate/job from the UI instead of picking an existing
  `candidate_id`/`job_id`) — would need a real write-path into `app/ingestion/source_models.py`'s
  tables, not built yet.

## Related Documents

- `docs/skill-extraction.md` — the CV upload endpoint this UI calls
- `docs/matching-engine.md` — the job-matches/candidate-matches endpoints this UI calls
- `docs/architecture.md` — overall system architecture
- `docs/progress.md` — full build history
