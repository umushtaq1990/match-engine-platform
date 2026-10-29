# Matching Engine (Phase 3 MVP)

Status: v0.1, implemented and verified against real seeded data. Covers Use Cases A, B, D from
`docs/prd.md`. Use Cases C (free-text extraction), E (course recommendations), and F (similarity
search) are deliberately not part of this slice — separate follow-up work, not crammed in here.

## Design principle: pure logic, thin DB adapter

`app/matching/` splits into:
- **Pure, DB-free modules** (`scoring.py`, `eligibility.py`, `skill_gap.py`): operate only on
  `CandidateProfile`/`JobProfile` dataclasses (`profiles.py`), never on SQLAlchemy models or a
  live session. This is what makes 27 of our tests run in under a second with no Postgres
  involved — the matching *rules* are isolated from data *access*.
- **The DB adapter** (`profiles.py` loaders, `rank.py`): translates ORM rows into those
  dataclasses and orchestrates ranking. This is the only place that talks to the database.

## Scoring (Use Cases A & B)

Formula (provisional weights, documented per the brief's instruction not to present weights as
objectively correct):

```
total = 0.55 * skill + 0.15 * location + 0.15 * salary + 0.15 * occupation
```

**Qualification compatibility is omitted entirely**, not faked — there is no candidate
qualifications/certifications table yet (see `docs/data-model.md` known limitations). The other
four weights are not re-normalized to compensate; this is itself a documented limitation of the
v0.1 formula, to revisit once that table exists.

| Component | Logic | Missing-data behavior |
|---|---|---|
| Skill | `0.7 * (required matched / required total) + 0.3 * (preferred matched / preferred total)`. A job with no required/preferred skills is trivially satisfied on that portion. | N/A — skill evidence is always explicit (present or absent in the set) |
| Location | 1.0 same city, 0.6 same region/different city, 0.0 otherwise | N/A — location is never null in this dataset |
| Salary | 1.0 if candidate/job ranges overlap, 0.0 if not | **0.5 (neutral)** if either side's salary data is missing — consistent with the "not recorded ≠ failing" principle from the data model |
| Occupation | 1.0 same occupation, 0.0 different | **0.5 (neutral)** if either side's occupation is missing |

## Eligibility (hard constraint, separate from scoring)

Per the brief: hard constraints must never be overridden by a high soft score. Implemented in
`eligibility.py`, evaluated before scoring. v0.1 has exactly one rule: a job with required skills
is **ineligible** (excluded entirely, not merely low-ranked) if the candidate matches *zero* of
them. This is a deliberately conservative placeholder — this synthetic dataset has no legal/
licensing/work-authorization data a real system would also need to check here.

## Skill-gap analysis (Use Case D)

For every missing required/preferred skill, `skill_gap.py` checks
`CandidateSkillAssessment` to classify it as:
- **`confirmed_absent`** — explicitly assessed and the candidate did not demonstrate it
- **`not_recorded`** — no evidence either way; unknown, not a confirmed gap

Verified via unit test: a skill the candidate has positive evidence for is never reported as a
gap, even if a *different* skill for the same candidate was separately assessed absent — the two
tables are checked independently per skill, not conflated.

## Verified against real data (not fabricated)

Ran both ranking directions against the live seeded Postgres instance:
- `rank_jobs_for_candidate(candidate_id=1)` → top 5 jobs, scores and explanations all computed
  from real rows (example: job #368 scored 0.58 total, broken into component scores).
- `rank_candidates_for_job(job_id=404)` → top 3 candidates; candidate #290 correctly shown with
  `missing required skills: Java` and skill-gap `not_recorded=['Java']`, while candidates #493/
  #1034 show a perfect skill match with an empty gap report.

## Course recommendations (Use Case E)

Different "gap" from Use Case D: this one is computed against a candidate's **target
occupation's** skill profile (`OccupationSkill`, built in Phase 2 — its `importance` field *is*
the skill-frequency/importance table this use case needs, reused rather than rebuilt), not
against a single job posting. A candidate's `primary_occupation_id` is treated as their target
role.

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

**MVP scope, documented**: ranks individual courses independently. Does not solve "minimal set of
courses to close the whole gap" (a set-cover problem) — a reasonable improvement to evaluate in
Phase 4/5, not built now.

**Verified against real data**: for candidate #1 (target occupation #30), gap = 3 skills not yet
demonstrated (Accounting, Java, Cybersecurity; none confirmed absent), total weighted gap 2.07.
Top recommendation: "Java Bootcamp" (coverage_score 0.43, Java as its primary anchor skill) —
correctly ranked above "React Fundamentals" (coverage_score 0.18), which only covers Java as one
of the generator's intentionally-injected *tangential* course skills, not its subject. This is the
scoring correctly discounting a weak/secondary match relative to a course centered on the actual
gap skill — not a bug, a demonstration of the formula working on realistically noisy data.

## Exposed via API (Phase 6)

All three ranking/recommendation entry points (`rank_jobs_for_candidate`,
`rank_candidates_for_job`, `recommend_courses_for_candidate`) are now reachable over HTTP, with no
business logic duplicated in the route layer — `app/api/routes.py` only translates between the
internal dataclasses and the wire-format Pydantic schemas (`app/api/schemas.py`):

| Endpoint | Use Case |
|---|---|
| `GET /candidates/{id}/job-matches?top_k=&include_ineligible=` | A |
| `GET /jobs/{id}/candidate-matches?top_k=&include_ineligible=` | B |
| `GET /candidates/{id}/course-recommendations?top_k=` | E |

All require the `X-API-Key` header (see `docs/security-and-threat-model.md`). A nonexistent
candidate/job id returns 404 (the existing `ValueError`s from `rank.py`/`course_recommendations.py`
translated at the route boundary, not duplicated validation).

**Verified against the live seeded database** (not just unit tests): built the updated
`docker/Dockerfile.api` (now also copying `app/matching/` and
`app/ingestion/{__init__.py,source_models.py}` — no new pip dependencies, image size barely moved,
289MB → 290MB), started it against the running `db` container, and confirmed over real `curl`
calls: 401 with no/wrong key, real ranked job matches with real explanations for candidate 1
(including a correctly-detected missing-required-skills case), real candidate matches for a job,
real course recommendations, and a 404 for a nonexistent candidate id.

## Known limitations (v0.1)

- **N+1 queries**: `load_all_job_profiles`/`load_all_candidate_profiles` issue one query per row.
  Fine for this dataset size (1000 jobs / 2000 candidates load in under a second), but a real
  bottleneck at larger scale. Documented as a Phase 9 performance task — not optimized now,
  before it's measured as an actual problem.
- **No qualification compatibility** (see above).
- **No semantic/embedding-based matching** — occupation similarity is exact-match-only (1.0/0.0),
  not "related occupations." Per the brief's instruction to introduce embeddings only once a
  rule-based baseline is proven insufficient (Phase 4/5 decision, not yet made).
- **Eligibility rule is a conservative placeholder**, not a real legal/licensing constraint model.
- Ranking currently loads *all* jobs/candidates into memory per call — fine at this scale, would
  need pagination/batching at real production volume.
