# Matching Engine

## Purpose & Scope

The deterministic, explainable rule-based matching baseline this whole project's "rule-based before ML" discipline (assumption A5) is built on: candidate↔job ranking, skill-gap analysis, and course recommendations. 
**In scope**: scoring, eligibility, skill-gap, course recommendations, and the HTTP API exposing them. 
**Out of scope** : the embeddings-blended re-ranking layered on top (its own doc, `docs/architecture/data-platform/embedding-matching.md`) and free-text skill extraction (`docs/architecture/application-services/skill-extraction.md`).

## Architecture & Design

**Pure logic, thin DB adapter**  `app/matching/` splits into:
- **Pure, DB-free modules** (`scoring.py`, `eligibility.py`, `skill_gap.py`): operate only on `CandidateProfile`/`JobProfile` dataclasses (`profiles.py`), never on SQLAlchemy models or a live session. This is what makes the majority of this module's tests run in under a second with no Postgres involved, the matching *rules* are isolated from data *access*.
- **The DB adapter** (`profiles.py` loaders, `rank.py`): translates ORM rows into those dataclasses and orchestrates ranking. This is the only place that talks to the database.

**Scoring formula** (provisional weights, documented per the brief's instruction not to present weights as objectively correct):

```
total = 0.7 * skill + 0.3 * education
```

**Issue #21 simplified this from the original four-component formula** (`0.55 * skill + 0.15 * location + 0.15 * salary + 0.15 * occupation`), per the user's explicit direction: location, salary, and occupation moved to filter/display-only fields on `Candidate`/`Job` (still readable via `GET /candidates/{id}/details`/the job endpoints, just no longer scored), replaced by a new education component. This also closes the brief's own previously-documented "qualification compatibility omitted entirely, no candidate qualifications table exists" gap (see Known Limitations history below) -- `Candidate.highest_degree_level`/`.field_of_study` and `Job.min_degree_level` are the new columns (migration `334b9feb41ce`), backfilled for the existing synthetic population by `app/ingestion/backfill_education.py` (curated-occupation rows only, domain-anchored like the existing skill backfill -- non-curated/no-occupation rows stay `NULL`, honest "unknown" rather than fabricated). The embeddings-blended ranker (`app/embeddings/retrieval.py`) made the identical change in the same pass, since it reused these same component functions -- see `docs/architecture/data-platform/embedding-matching.md`'s Round 5 for the re-measured NDCG impact.

| Component | Logic | Missing-data behavior |
|---|---|---|
| Skill | `0.7 * (required matched / required total) + 0.3 * (preferred matched / preferred total)`. A job with no required/preferred skills is trivially satisfied on that portion. | N/A, skill evidence is always explicit (present or absent in the set) |
| Education | 1.0 if the job has no degree requirement, or the candidate's highest degree meets/exceeds it (ordinal comparison, high_school < associate < bachelor < master < doctorate); 0.0 if it falls short | **0.5 (neutral)** if the job has a requirement but the candidate's degree level isn't recorded, consistent with the "not recorded ≠ failing" principle from the data model |

**Eligibility** (hard constraint, separate from scoring): per the brief, hard constraints must never be overridden by a high soft score. Implemented in `eligibility.py`, evaluated before scoring. One rule: a job with required skills is **ineligible** (excluded entirely, not merely low-ranked) if the candidate matches *zero* of them. A deliberately conservative placeholder, this synthetic dataset has no legal/licensing/work-authorization data a real system would also need to
check here.

**Skill-gap analysis** (Use Case D): for every missing required/preferred skill, `skill_gap.py` checks `CandidateSkillAssessment` to classify it as:
- **`confirmed_absent`**: explicitly assessed and the candidate did not demonstrate it
- **`not_recorded`**: no evidence either way; unknown, not a confirmed gap

**Course recommendations** (Use Case E): a different "gap" from Use Case D, computed against a candidate's **target occupation's** skill profile (`OccupationSkill`, whose `importance` field *is* the skill-frequency/importance table this use case needs, reused rather than rebuilt), not against a single job posting. A candidate's `primary_occupation_id` is treated as their target role. `app/matching/course_recommendations.py`:
- `analyze_occupation_skill_gap()`: splits the occupation's skill profile into `present` (candidate already has it), `confirmed_absent`, and `not_recorded`, same evidence distinction as Use Case D.
- `recommend_courses()`: ranks courses by `coverage_score`: the share of the candidate's *importance-weighted* total gap that a single course covers (`CourseSkill.coverage_weight` × `OccupationSkill.importance`, summed over covered skills, normalized by total gap weight).
- `recommend_courses_for_candidate()`: DB-aware entry point. Raises explicitly (not a silent empty result) if the candidate has no `primary_occupation_id`, about 5% of the synthetic dataset by design (see `docs/architecture/data-platform/data-model.md`), so this use case doesn't apply to every candidate.

**MVP scope, documented**: course recommendations rank individual courses independently. Does not solve "minimal set of courses to close the whole gap" (a set-cover problem), a reasonable future improvement, not built now.

## Implementation

All three ranking/recommendation entry points (`rank_jobs_for_candidate`, `rank_candidates_for_job`, `recommend_courses_for_candidate`) are reachable over HTTP, with no business logic duplicated in the route layer, `app/api/routes.py` only translates between the internal dataclasses and the wire-format Pydantic schemas (`app/api/schemas.py`):

| Endpoint | Use Case |
|---|---|
| `GET /candidates/{id}/job-matches?top_k=&include_ineligible=&mode=` | A (rule-based, or the default embeddings-blended re-ranking, see `docs/architecture/data-platform/embedding-matching.md`) |
| `GET /jobs/{id}/candidate-matches?top_k=&include_ineligible=` | B |
| `GET /candidates/{id}/course-recommendations?top_k=` | E |

All require the `X-API-Key` header (see `docs/security-and-threat -model.md`). A nonexistent candidate/job id returns 404 (the existing `ValueError`s from `rank.py`/`course_recommendations.py` translated at the route boundary, not duplicated validation).

**Performance**: `load_all_job_profiles`/`load_all_candidate_profiles` batch-load each entity table plus its child skill tables in 2-3 queries total, then group in memory, rather than issuing one query per row, see Verification & Results below for the measured impact.

## Verification & Results

Ran both ranking directions against the live seeded Postgres instance:
- `rank_jobs_for_candidate(candidate_id=1)` → top 5 jobs, scores and explanations all computed from real rows (example: job #368 scored 0.58 total, broken into component scores).
- `rank_candidates_for_job(job_id=404)` → top 3 candidates; candidate #290 correctly shown with `missing required skills: Java` and skill-gap `not_recorded=['Java']`, while candidates #493/ #1034 show a perfect skill match with an empty gap report.

Skill-gap analysis verified via unit test: a skill the candidate has positive evidence for is never reported as a gap, even if a *different* skill for the same candidate was separately assessed absent, the two tables are checked independently per skill, not conflated.

Course recommendations verified against real data: for candidate #1 (target occupation #30), gap = 3 skills not yet demonstrated (Accounting, Java, Cybersecurity; none confirmed absent), total weighted gap 2.07. Top recommendation: "Java Bootcamp" (coverage_score 0.43, Java as its primary anchor skill), correctly ranked above "React Fundamentals" (coverage_score 0.18), which only covers Java as one of the generator's intentionally-injected *tangential* course skills, not its subject. This is the scoring correctly discounting a weak/secondary match relative to a course centered on the actual gap skill not a bug, a demonstration of the formula working on realistically noisy data.

**API exposure verified against the live seeded database**: built `docker/Dockerfile.api` (copying `app/matching/` and `app/ingestion/{__init__.py,source_models.py}`,  no new pip dependencies, image size barely moved, 289MB → 290MB), started it against the running `db` container, and confirmed over real `curl` calls: 401 with no/wrong key, real ranked job matches with real explanations for candidate 1 (including a correctly-detected missing-required- skills case), real candidate matches for a job, real course recommendations, and a 404 for a nonexistent candidate id.

**Performance fix — a real N+1 bug**: `load_all_job_profiles`/
`load_all_candidate_profiles` originally issued one query per row (1000+1 / 2000+1 queries). Fixed by batch-loading, verified against the original single-entity loaders (`load_job_profile`/`load_candidate_profile`, left unchanged) with zero output mismatches across a 25-row sample of each. `rank_jobs_for_candidate` dropped from ~1.95s/candidate (the measurement that originally forced a 50-candidate evaluation sample) to ~0.06s/candidate, a ~33x speedup. This is what unblocked full-population evaluation (see `docs/architecture/ai-ml-and-mlops.md`).

**Issue #21 (skills + education scoring) verified live**: ran the new migration (`334b9feb41ce`) and `app/ingestion/backfill_education.py` against the real seeded database -- 3000 jobs and 1000 candidates updated on the first run (curated-occupation rows only), confirmed idempotent on a second run (`0 updated`), and spot-checked via `psql` that non-curated/no-occupation rows stay `NULL`. Hit the real API through a genuine Keycloak-issued token for an existing golden candidate: `GET /candidates/{id}/job-matches` returned the new `{"skill", "education", "total"}` score shape; `GET`/`PATCH /candidates/{id}/details` round-tripped `highest_degree_level`/`field_of_study` correctly; `GET /candidates/{id}/job-matches/semantic` returned the new `education_score` field. Full suite (341 tests) and `ruff check .` both pass after the change.

## Known Limitations

- **Occupation similarity is no longer part of the score at all** (issue #21 -- see above); previously exact-match-only (1.0/0.0) in the rule-based score, with "related occupations" resolved separately by the embeddings-blended ranking path. That path's own `occupation_score` field was removed in the same change (see `docs/architecture/data-platform/embedding-matching.md`'s Round 5), since it reused this module's now-deleted `_occupation_component`.
- **Eligibility rule is a conservative placeholder**, not a real legal/licensing constraint model.
- Ranking currently loads *all* jobs/candidates into memory per call, fine at this scale (verified up to the real 1M-candidate/50K-job target via the two-stage retrieve-then-score path for embeddings, see `docs/architecture/data-platform/data-at-scale.md`), but the pure rule-based path itself would need pagination/batching at that volume without the ANN narrowing step.

## Resource Footprint and Scaling

Real measured concurrency numbers (not a single-request estimate): at 1 uvicorn worker, throughput plateaued at ~14-16 req/s regardless of concurrency while latency scaled linearly (a serialized CPU-bound bottleneck, confirmed via `docker/Dockerfile.api`'s single worker process). Adding `--workers 4` fixed it: throughput 14.2 -> 56.1 req/s at concurrency 20, p95 1.84s -> 0.57s, see `docs/operations/production-readiness.md` for the full load-test table. This is the one service in the platform with a working k8s scaling path today: 2 replicas + an HPA to 5 (`k8s/base/api.yaml`), inert locally without `metrics-server` but architecturally real. Full cross-layer scenario: `docs/operations/capacity-planning.md`.

## Related Documents

- `docs/architecture.md` : overview and runtime views
- `docs/architecture/data-platform/embedding-matching.md` : the embeddings-blended ranking path layered on top of this engine
- `docs/architecture/data-platform/data-model.md` : schema this engine reads from
- `docs/architecture/ai-ml-and-mlops.md` : how matching quality is measured
- `docs/security-and-threat-model.md` : the `X-API-Key` auth these endpoints require
- `docs/assumptions-register.md` : assumption A5 (rule-based before ML)
