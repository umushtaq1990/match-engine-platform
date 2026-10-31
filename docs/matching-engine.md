# Matching Engine

Status: v1, implemented and verified against real seeded data. Covers Use Cases A, B, D (rule-based
matching, inverse direction, skill-gap analysis) and E (course recommendations). Use Case F
(similarity search) is partially covered by the embeddings-blended ranking path — see
`docs/embedding-matching.md`; Use Case C (free-text extraction) lives in `docs/skill-extraction.md`.

## Purpose & Scope

The deterministic, explainable rule-based matching baseline this whole project's "rule-based
before ML" discipline (assumption A5) is built on: candidate↔job ranking, skill-gap analysis, and
course recommendations. In scope: scoring, eligibility, skill-gap, course recommendations, and the
HTTP API exposing them. Out of scope here: the embeddings-blended re-ranking layered on top (its
own doc, `docs/embedding-matching.md`) and free-text skill extraction (`docs/skill-extraction.md`).

## Architecture & Design

**Pure logic, thin DB adapter** — `app/matching/` splits into:
- **Pure, DB-free modules** (`scoring.py`, `eligibility.py`, `skill_gap.py`): operate only on
  `CandidateProfile`/`JobProfile` dataclasses (`profiles.py`), never on SQLAlchemy models or a
  live session. This is what makes the majority of this module's tests run in under a second with
  no Postgres involved — the matching *rules* are isolated from data *access*.
- **The DB adapter** (`profiles.py` loaders, `rank.py`): translates ORM rows into those
  dataclasses and orchestrates ranking. This is the only place that talks to the database.

**Scoring formula** (provisional weights, documented per the brief's instruction not to present
weights as objectively correct):

```
total = 0.55 * skill + 0.15 * location + 0.15 * salary + 0.15 * occupation
```

**Qualification compatibility is omitted entirely**, not faked — there is no candidate
qualifications/certifications table yet (see `docs/data-model.md` known limitations). The other
four weights are not re-normalized to compensate; this is itself a documented limitation of the
formula.

| Component | Logic | Missing-data behavior |
|---|---|---|
| Skill | `0.7 * (required matched / required total) + 0.3 * (preferred matched / preferred total)`. A job with no required/preferred skills is trivially satisfied on that portion. | N/A — skill evidence is always explicit (present or absent in the set) |
| Location | 1.0 same city, 0.6 same region/different city, 0.0 otherwise | N/A — location is never null in this dataset |
| Salary | 1.0 if candidate/job ranges overlap, 0.0 if not | **0.5 (neutral)** if either side's salary data is missing — consistent with the "not recorded ≠ failing" principle from the data model |
| Occupation | 1.0 same occupation, 0.0 different | **0.5 (neutral)** if either side's occupation is missing |

**Eligibility** (hard constraint, separate from scoring): per the brief, hard constraints must
never be overridden by a high soft score. Implemented in `eligibility.py`, evaluated before
scoring. One rule: a job with required skills is **ineligible** (excluded entirely, not merely
low-ranked) if the candidate matches *zero* of them. A deliberately conservative placeholder — this
synthetic dataset has no legal/licensing/work-authorization data a real system would also need to
check here.

**Skill-gap analysis** (Use Case D): for every missing required/preferred skill, `skill_gap.py`
checks `CandidateSkillAssessment` to classify it as:
- **`confirmed_absent`** — explicitly assessed and the candidate did not demonstrate it
- **`not_recorded`** — no evidence either way; unknown, not a confirmed gap

**Course recommendations** (Use Case E): a different "gap" from Use Case D — computed against a
candidate's **target occupation's** skill profile (`OccupationSkill`, whose `importance` field *is*
the skill-frequency/importance table this use case needs, reused rather than rebuilt), not against
a single job posting. A candidate's `primary_occupation_id` is treated as their target role.
`app/matching/course_recommendations.py`:
- `analyze_occupation_skill_gap()` — splits the occupation's skill profile into `present`
  (candidate already has it), `confirmed_absent`, and `not_recorded`, same evidence distinction as
  Use Case D.
- `recommend_courses()` — ranks courses by `coverage_score`: the share of the candidate's
  *importance-weighted* total gap that a single course covers (`CourseSkill.coverage_weight` ×
  `OccupationSkill.importance`, summed over covered skills, normalized by total gap weight).
- `recommend_courses_for_candidate()` — DB-aware entry point. Raises explicitly (not a silent
  empty result) if the candidate has no `primary_occupation_id` — about 5% of the synthetic
  dataset by design (see `docs/data-model.md`), so this use case doesn't apply to every candidate.

**MVP scope, documented**: course recommendations rank individual courses independently. Does not
solve "minimal set of courses to close the whole gap" (a set-cover problem) — a reasonable future
improvement, not built now.

## Implementation

All three ranking/recommendation entry points (`rank_jobs_for_candidate`,
`rank_candidates_for_job`, `recommend_courses_for_candidate`) are reachable over HTTP, with no
business logic duplicated in the route layer — `app/api/routes.py` only translates between the
internal dataclasses and the wire-format Pydantic schemas (`app/api/schemas.py`):

| Endpoint | Use Case |
|---|---|
| `GET /candidates/{id}/job-matches?top_k=&include_ineligible=&mode=` | A (rule-based, or the default embeddings-blended re-ranking — see `docs/embedding-matching.md`) |
| `GET /jobs/{id}/candidate-matches?top_k=&include_ineligible=` | B |
| `GET /candidates/{id}/course-recommendations?top_k=` | E |

All require the `X-API-Key` header (see `docs/security-and-threat-model.md`). A nonexistent
candidate/job id returns 404 (the existing `ValueError`s from `rank.py`/`course_recommendations.py`
translated at the route boundary, not duplicated validation).

**Performance**: `load_all_job_profiles`/`load_all_candidate_profiles` batch-load each entity table
plus its child skill tables in 2-3 queries total, then group in memory, rather than issuing one
query per row — see Verification & Results below for the measured impact.

## Verification & Results

Ran both ranking directions against the live seeded Postgres instance:
- `rank_jobs_for_candidate(candidate_id=1)` → top 5 jobs, scores and explanations all computed
  from real rows (example: job #368 scored 0.58 total, broken into component scores).
- `rank_candidates_for_job(job_id=404)` → top 3 candidates; candidate #290 correctly shown with
  `missing required skills: Java` and skill-gap `not_recorded=['Java']`, while candidates #493/
  #1034 show a perfect skill match with an empty gap report.

Skill-gap analysis verified via unit test: a skill the candidate has positive evidence for is never
reported as a gap, even if a *different* skill for the same candidate was separately assessed
absent — the two tables are checked independently per skill, not conflated.

Course recommendations verified against real data: for candidate #1 (target occupation #30), gap =
3 skills not yet demonstrated (Accounting, Java, Cybersecurity; none confirmed absent), total
weighted gap 2.07. Top recommendation: "Java Bootcamp" (coverage_score 0.43, Java as its primary
anchor skill) — correctly ranked above "React Fundamentals" (coverage_score 0.18), which only
covers Java as one of the generator's intentionally-injected *tangential* course skills, not its
subject. This is the scoring correctly discounting a weak/secondary match relative to a course
centered on the actual gap skill — not a bug, a demonstration of the formula working on
realistically noisy data.

**API exposure verified against the live seeded database** (not just unit tests): built
`docker/Dockerfile.api` (copying `app/matching/` and `app/ingestion/{__init__.py,source_models.py}`
— no new pip dependencies, image size barely moved, 289MB → 290MB), started it against the running
`db` container, and confirmed over real `curl` calls: 401 with no/wrong key, real ranked job
matches with real explanations for candidate 1 (including a correctly-detected missing-required-
skills case), real candidate matches for a job, real course recommendations, and a 404 for a
nonexistent candidate id.

**Performance fix — a real N+1 bug, measured not estimated**: `load_all_job_profiles`/
`load_all_candidate_profiles` originally issued one query per row (1000+1 / 2000+1 queries). Fixed
by batch-loading, verified against the original single-entity loaders (`load_job_profile`/
`load_candidate_profile`, left unchanged) with zero output mismatches across a 25-row sample of
each. `rank_jobs_for_candidate` dropped from ~1.95s/candidate (the measurement that originally
forced a 50-candidate evaluation sample) to ~0.06s/candidate — a ~33x speedup. This is what
unblocked full-population evaluation (see `docs/evaluation-strategy.md`).

## Known Limitations

- **No qualification compatibility** (see above).
- **Occupation similarity is exact-match-only** (1.0/0.0) in the rule-based score, not "related
  occupations" — **resolved separately** by the embeddings-blended ranking path, now the default
  for Use Case A (`docs/embedding-matching.md`); the rule-based score itself is unchanged.
- **Eligibility rule is a conservative placeholder**, not a real legal/licensing constraint model.
- Ranking currently loads *all* jobs/candidates into memory per call — fine at this scale (verified
  up to the real 1M-candidate/50K-job target via the two-stage retrieve-then-score path for
  embeddings, see `docs/data-at-scale.md`), but the pure rule-based path itself would need
  pagination/batching at that volume without the ANN narrowing step.

## Related Documents

- `docs/architecture.md` — overview and runtime views
- `docs/progress.md` — full build history
- `docs/embedding-matching.md` — the embeddings-blended ranking path layered on top of this engine
- `docs/data-model.md` — schema this engine reads from, including the qualifications-table gap
- `docs/evaluation-strategy.md` — how matching quality is measured
- `docs/security-and-threat-model.md` — the `X-API-Key` auth these endpoints require
- `docs/assumptions-register.md` — assumption A5 (rule-based before ML)
