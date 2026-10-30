# Data Model — JME Portfolio Platform

Status: v0.2, implemented and verified. Schema lives in `app/ingestion/source_models.py`, managed
via Alembic (`alembic/versions/`). Generator: `app/ingestion/generate_synthetic_data.py` (paths
updated after the Phase 2 follow-up restructuring into `app/ingestion/`/`app/api/` — see ADR 0001).

## Entity-relationship summary

```
Skill ──< SkillSynonym
Skill ──< OccupationSkill >── Occupation
Skill ──< CourseSkill >── Course
Skill ──< CandidateSkill >── Candidate
Skill ──< CandidateSkillAssessment >── Candidate
Skill ──< JobSkill >── Job
Occupation ──< Candidate (primary_occupation_id, nullable)
Occupation ──< Job (occupation_id)
```

## Tables

| Table | Purpose | Key fields |
|---|---|---|
| `skills` | Canonical skill taxonomy | `name` (unique), `category` |
| `skill_synonyms` | Raw/messy text variants mapping to a canonical skill (feeds Use Case C extraction) | `synonym_text` (unique), `skill_id` FK |
| `occupations` | Occupation taxonomy | `code` (unique), `title` |
| `occupation_skills` | Expected skill profile per occupation, weighted | `occupation_id`, `skill_id`, `importance` (0-1) |
| `courses` | Course/certification catalog | `title`, `provider`, `level`, `duration_hours` |
| `course_skills` | Skills a course covers, weighted | `course_id`, `skill_id`, `coverage_weight` (0-1) |
| `candidates` | Job seekers | `location_city/region`, `years_experience`, `desired_salary_min/max` (nullable), `primary_occupation_id` (nullable) |
| `candidate_skills` | **Positive** skill evidence only | `proficiency` enum, `evidence_source` enum (self_reported/assessed/inferred) |
| `candidate_skill_assessments` | Explicit assessment outcome, independent of `candidate_skills` | `result` enum (present/absent), `assessed_at` |
| `jobs` | Job postings | `title`, `company_name`, `occupation_id`, `description_raw`, `salary_min/max` (nullable) |
| `job_skills` | Requirements per job | `requirement_level` enum (required/preferred) |

## Design decision: distinguishing "not recorded" from "assessed absent"

This was an explicit requirement (PRD Use Case D / brief Phase 3). It is modeled with **two
separate tables**, not a single nullable/tri-state flag on one table:

- A row in `candidate_skills` = positive evidence the candidate has the skill.
- **No row** in `candidate_skills` for a given (candidate, skill) pair = *not recorded* — unknown,
  not "absent."
- A row in `candidate_skill_assessments` with `result = absent` = the skill was *actively checked*
  and the candidate did not demonstrate it — genuine negative evidence.

Skill-gap logic (Phase 3) must query both tables and treat "no evidence either way" as a distinct
third state from "confirmed absent," per the brief's explicit instruction not to assume missing
data means a candidate lacks a skill.

## Synthetic dataset scale (as generated, measured — not placeholder)

| Entity | Count |
|---|---|
| Candidates | 2,000 |
| Jobs | 1,000 |
| Occupations | 150 |
| Courses | 100 |
| Skills (canonical taxonomy) | 64 — curated, meaningful names across programming/data/trades/healthcare/business/soft-skill categories, not padded to an arbitrary round number |
| Skill synonyms | 15 (seed set; to expand as Use Case C extraction is built) |
| Candidate-skill rows | 11,814 |
| Candidate skill assessments | 402 |
| Job-skill requirement rows | 6,008 |
| Occupation-skill profile rows | 908 |
| Course-skill coverage rows | 195 |

Deviation from the original A4 assumption: the skill taxonomy target of "~300" was abandoned in
favor of 64 *real, meaningful* skill names. A larger taxonomy of meaningless auto-generated skill
names would not have served the project's goals (explainability, realistic matching) and was
judged worse than a smaller curated set. `docs/assumptions-register.md` A4 updated accordingly.

## Reproducibility (verified)

The generator uses `Faker.seed(42)` and `random.Random(42)`. Verified by hashing the full
candidates table (`full_name || email`, ordered by id) before and after a repeat run against a
freshly reset schema: **identical MD5 hash both times**. The generator is deterministic.

## Intentional data imperfections (verified against generated data)

| Imperfection | Target rate | Measured rate |
|---|---|---|
| Candidates missing `primary_occupation_id` | ~5% | 101/2000 = 5.05% |
| Candidates missing desired salary | ~15% | 305/2000 = 15.25% |
| Jobs missing salary range | ~10% | 89/1000 = 8.9% |
| Skill synonyms (messy text variants) | seed set | 15 variants across 7 skills |

These are deliberate, per the brief's instruction that the dataset must not be "so clean that the
matching system becomes trivial."

## Known limitations / not yet modeled

- ~~No duplicate-candidate or duplicate-job detection logic yet~~ **RESOLVED (Phase 2 follow-up)**:
  `data_quality.py`'s `duplicate_candidate_emails`/`duplicate_candidate_identity` checks cover
  this — see `docs/data-quality.md`.
- No explicit candidate qualifications/certifications table — **not a pending gap, a permanent
  design choice**: `app/matching/scoring.py` deliberately omits qualification compatibility from
  the scoring formula entirely (weights renormalized over the four components that do exist)
  rather than faking it with a placeholder, documented explicitly in that module and in
  `docs/matching-engine.md`. Would only change if a real qualifications data source existed to
  model.
- No interaction/application history table (optional, only relevant if collaborative-filtering
  course recommendations are ever pursued — not scheduled in any phase).
- `job_skills` requirement coverage is derived from the job's occupation profile, not from parsing
  `description_raw`. As of Phase 16, `description_raw` is generated *from* those same `job_skills`
  rows (weaving each job's real required/preferred skills into its description text, by canonical
  name or synonym — see `app/ingestion/generate_synthetic_data.py`), specifically so free-text
  skill extraction (Use Case C, `docs/skill-extraction.md`) has real ground truth to extract
  against. Extraction output is not yet wired back into `job_skills` itself — that's Use Case C's
  own v3, not built yet.
