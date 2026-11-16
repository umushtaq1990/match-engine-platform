"""golden-dataset v2: adds the new curated occupations (app/ingestion/
generate_synthetic_data.py's OCCUPATION_TITLES[26:], ~43 titles spanning
the 8 CURATED_OCCUPATION_DOMAINS) with a correctly, deterministically
domain-anchored occupation_skills profile -- the actual fix for the bug
this effort started from (a "Welder" occupation whose required skill was
"Django," because the base seed anchors each occupation_skills profile to
a domain chosen uniformly at random, independent of the title).

Purely additive: never touches the original 150 base-seed occupations (the
26 original curated titles included) or any existing candidate/job/skill
assignment -- only inserts new Occupation/OccupationSkill rows, plus the
handful of new Skill rows (GOLDEN_V2_NEW_SKILLS) needed to give the newly-
expanded domains (sales_marketing, healthcare, education,
logistics_operations) enough breadth to anchor-sample a believable
k=4-8 profile from, and retags the pre-existing "Sales" skill from its
original "business" category to the new "sales" category (a label change
only -- same Skill.id, no candidate_skills/job_skills row is affected).

Idempotent: skips any title that already has an Occupation row (by exact
title match) and any skill that already exists (by name).

Run with: docker compose run --rm golden-curated-occupations
"""
import argparse
import random

from sqlalchemy import select

from app.db import SessionLocal
from app.ingestion.generate_synthetic_data import (
    CURATED_OCCUPATION_DOMAINS,
    GOLDEN_V2_NEW_SKILLS,
    OCCUPATION_TITLES,
    RETAGGED_SKILL_CATEGORIES,
    SKILL_DOMAINS,
    SKILL_TYPE_OF,
)
from app.ingestion.source_models import Occupation, OccupationSkill, Skill

SEED = 4242

# Indices 0-25 are the original curated titles (already seeded, left
# untouched); everything from 26 on is new for golden-dataset v2.
NEW_TITLES = OCCUPATION_TITLES[26:]


def _ensure_new_skills(session) -> dict[str, Skill]:
    skills_by_name = {s.name: s for s in session.execute(select(Skill)).scalars().all()}
    for name, category in GOLDEN_V2_NEW_SKILLS:
        if name in skills_by_name:
            continue
        skill = Skill(name=name, category=category, skill_type=SKILL_TYPE_OF.get(category))
        session.add(skill)
        skills_by_name[name] = skill
    session.flush()
    return skills_by_name


def _retag_existing_skills(skills_by_name: dict[str, Skill]) -> None:
    for name, new_category in RETAGGED_SKILL_CATEGORIES.items():
        skill = skills_by_name.get(name)
        if skill is None:
            continue
        skill.category = new_category
        skill.skill_type = SKILL_TYPE_OF.get(new_category)


def _assign_fixed_domain_skills(rng, all_skills, domain_pool, k):
    """Same 70-80%-anchor/rest-noise mechanic as generate_synthetic_data.py's
    _assign_domain_anchored_skills, but anchored to a GIVEN domain pool
    instead of `rng.choice(domain_names)` -- the actual root fix."""
    n_anchor = min(max(1, round(k * rng.uniform(0.7, 0.8))), len(domain_pool), k)
    anchor_skills = rng.sample(domain_pool, k=n_anchor)
    anchor_ids = {s.id for s in anchor_skills}
    remaining_pool = [s for s in all_skills if s.id not in anchor_ids]
    n_noise = min(k - n_anchor, len(remaining_pool))
    noise_skills = rng.sample(remaining_pool, k=n_noise) if n_noise else []
    return anchor_skills + noise_skills


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()

    session = SessionLocal()
    try:
        skills_by_name = _ensure_new_skills(session)
        _retag_existing_skills(skills_by_name)
        session.commit()

        all_skills = session.execute(select(Skill)).scalars().all()
        skills_by_domain = {
            domain: [s for s in all_skills if s.category in categories]
            for domain, categories in SKILL_DOMAINS.items()
        }

        existing_titles = {o.title for o in session.execute(select(Occupation)).scalars().all()}

        rng = random.Random(SEED)
        inserted = skipped = 0
        for new_index, title in enumerate(NEW_TITLES):
            if title in existing_titles:
                skipped += 1
                continue

            domain = CURATED_OCCUPATION_DOMAINS[title]
            # occupations.code is String(20) -- too short for a slugified
            # title (e.g. "OCC-CURATED-CLOUD-ENGINEER" overflows it). Also
            # can't reuse the base seed's own "OCC-0000".."OCC-0149" scheme
            # (those codes are already taken by the 150 base occupations,
            # padding duplicates included) -- a distinct "OCC-NEW-" prefix
            # avoids the collision.
            code = f"OCC-NEW-{new_index:04d}"
            occ = Occupation(code=code, title=title, is_curated=True, domain=domain)
            session.add(occ)
            session.flush()

            k = rng.randint(4, 8)
            profile_skills = _assign_fixed_domain_skills(rng, all_skills, skills_by_domain[domain], k)
            for sk in profile_skills:
                session.add(OccupationSkill(
                    occupation_id=occ.id, skill_id=sk.id, importance=round(rng.uniform(0.5, 1.0), 2),
                ))
            inserted += 1

        session.commit()
        print(f"Occupations inserted: {inserted}, skipped (already present): {skipped}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
