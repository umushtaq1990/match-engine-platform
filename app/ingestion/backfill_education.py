"""Issue #21: one-off backfill of `Candidate.highest_degree_level`/
`field_of_study` and `Job.min_degree_level` for the existing base seed pool.

Without this, every education score would be `UNKNOWN_DATA_SCORE` (candidate
side has no data) or trivially 1.0 (job side has no requirement) -- no real
signal, and re-measuring NDCG after the scoring change
(docs/architecture/data-platform/embedding-matching.md) would show nothing
changed. Purely additive, like app/golden/seed_curated_occupations.py:
touches only the three new nullable columns, on existing rows, and only for
rows resolvable to a curated occupation (`Occupation.domain IS NOT NULL`,
the same test `app/golden/select.py` already uses) -- everything else (no
occupation, or a non-curated "padding" occupation) is left `NULL`, honest
"unknown" rather than fabricated.

Candidates get their `highest_degree_level` anchored to their OWN target
occupation's domain with the same 70/30-ish anchored/noise split
`_assign_domain_anchored_skills` already uses for skills -- anchoring every
candidate exactly to their occupation's minimum would make every curated
candidate trivially satisfy their own occupation's requirement, and the
score would never differentiate anyone.

Deterministic seeded RNG, idempotent (skips any row that already has a
value) -- same reusable pattern as every other backfill/seed script in
app/golden/ and here.

Run with: docker compose run --rm backfill-education
"""
import argparse
import random

from sqlalchemy import select

from app.db import SessionLocal
from app.ingestion.source_models import Candidate, DegreeLevel, Job, Occupation

SEED = 9001

# Hand-curated, per-domain minimum degree -- same judgment-call spirit as
# app/ingestion/generate_synthetic_data.py's CURATED_OCCUPATION_DOMAINS.
DOMAIN_MIN_DEGREE = {
    "technology": DegreeLevel.bachelor,
    "healthcare": DegreeLevel.bachelor,
    "business_finance": DegreeLevel.bachelor,
    "education": DegreeLevel.bachelor,
    "creative_media": DegreeLevel.associate,
    "skilled_trades": DegreeLevel.high_school,
    "sales_marketing": DegreeLevel.high_school,
    "logistics_operations": DegreeLevel.high_school,
}

# Ordinal rank, same table as app/matching/scoring.py's _DEGREE_LEVEL_RANK --
# duplicated rather than imported, since that one is scoring.py's own
# private (`_`-prefixed) detail.
_DEGREE_LEVEL_RANK = {
    DegreeLevel.high_school: 0,
    DegreeLevel.associate: 1,
    DegreeLevel.bachelor: 2,
    DegreeLevel.master: 3,
    DegreeLevel.doctorate: 4,
}
_DEGREE_LEVELS_BY_RANK = sorted(_DEGREE_LEVEL_RANK, key=_DEGREE_LEVEL_RANK.get)

# Small, representative field-of-study strings per domain -- display-only
# (no scoring logic reads field_of_study), so this stays deliberately simple.
DOMAIN_FIELDS_OF_STUDY = {
    "technology": ["Computer Science", "Software Engineering", "Information Technology"],
    "healthcare": ["Nursing", "Health Sciences", "Biology"],
    "business_finance": ["Business Administration", "Finance", "Accounting"],
    "education": ["Education", "Curriculum and Instruction"],
    "creative_media": ["Graphic Design", "Communications", "Fine Arts"],
    "skilled_trades": ["Industrial Technology", "Applied Trades"],
    "sales_marketing": ["Marketing", "Business Administration"],
    "logistics_operations": ["Supply Chain Management", "Operations Management"],
}

# ~70% of candidates anchor to their own occupation's domain minimum (or
# above); the rest get a uniform-random level from the full scale --
# otherwise every curated candidate would trivially satisfy their own
# occupation's requirement and the education score would never
# differentiate anyone, same rationale as
# generate_synthetic_data.py's _assign_domain_anchored_skills.
ANCHOR_PROBABILITY = 0.7


def _candidate_degree_level(rng: random.Random, domain_min: DegreeLevel) -> DegreeLevel:
    if rng.random() < ANCHOR_PROBABILITY:
        min_rank = _DEGREE_LEVEL_RANK[domain_min]
        return rng.choice(_DEGREE_LEVELS_BY_RANK[min_rank:])
    return rng.choice(_DEGREE_LEVELS_BY_RANK)


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()

    session = SessionLocal()
    try:
        curated_domain_by_occupation_id = {
            o.id: o.domain
            for o in session.execute(select(Occupation).where(Occupation.domain.isnot(None))).scalars()
        }

        rng = random.Random(SEED)

        jobs_updated = jobs_skipped = 0
        for job in session.execute(select(Job)).scalars():
            domain = curated_domain_by_occupation_id.get(job.occupation_id)
            if job.min_degree_level is not None or domain is None:
                jobs_skipped += 1
                continue
            job.min_degree_level = DOMAIN_MIN_DEGREE[domain]
            jobs_updated += 1

        candidates_updated = candidates_skipped = 0
        for candidate in session.execute(select(Candidate)).scalars():
            domain = curated_domain_by_occupation_id.get(candidate.primary_occupation_id)
            if candidate.highest_degree_level is not None or domain is None:
                candidates_skipped += 1
                continue
            candidate.highest_degree_level = _candidate_degree_level(rng, DOMAIN_MIN_DEGREE[domain])
            candidate.field_of_study = rng.choice(DOMAIN_FIELDS_OF_STUDY[domain])
            candidates_updated += 1

        session.commit()
        print(
            f"Jobs updated: {jobs_updated}, skipped (already set or no curated occupation): "
            f"{jobs_skipped}"
        )
        print(
            f"Candidates updated: {candidates_updated}, skipped (already set or no curated "
            f"occupation): {candidates_skipped}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
