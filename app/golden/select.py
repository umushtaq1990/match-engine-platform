"""Deterministic selection of the "golden" candidates -- a pure function
over already-seeded data, not a stored selection, so every golden-dataset
script (CVs, jobs, logins, persona events, eval harnesses) re-derives the
exact same ids without needing a manifest file or extra table.

golden-dataset v2 adds `select_and_assign_golden_candidates`: unlike
`select_golden_candidate_ids` below (which only *reads* whatever
`primary_occupation_id` a candidate already has from the base seed --
assigned at random, which is exactly what let a "Welder"-titled occupation
end up with "Django" as a required skill, confirmed live this session),
this one *assigns* each selected candidate to one of the new curated,
correctly domain-anchored occupations (app/golden/
seed_curated_occupations.py) and regenerates their CandidateSkill/
CandidateSkillAssessment rows to match -- the actual root fix, not a
relabeling. Only touches the candidates it selects; nothing else in the
base 2000-candidate pool is read or written.
"""
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.golden.models import GoldenPair
from app.golden.seed_curated_occupations import NEW_TITLES, _assign_fixed_domain_skills
from app.ingestion.generate_synthetic_data import SKILL_DOMAINS
from app.ingestion.source_models import (
    AssessmentResult,
    Candidate,
    CandidateSkill,
    CandidateSkillAssessment,
    EvidenceSource,
    Occupation,
    ProficiencyLevel,
    Skill,
)

DEFAULT_N = 500
MIN_SKILLS = 3
GOLDEN_V2_DEFAULT_N = 1000
GOLDEN_V2_SEED = 9001


def select_golden_candidate_ids(session: Session, n: int = DEFAULT_N) -> list[int]:
    """Candidates with a real occupation and enough skills to build a
    believable CV/job pair from, ordered by (primary_occupation_id, id) and
    strided across that ordering so the selection spreads evenly across
    occupations instead of favoring whichever occupation happens to sort
    first -- a stratified sample, not a prefix of the sorted list.
    """
    skill_counts = (
        select(CandidateSkill.candidate_id, func.count().label("n"))
        .group_by(CandidateSkill.candidate_id)
        .having(func.count() >= MIN_SKILLS)
        .subquery()
    )
    eligible_ids = session.execute(
        select(Candidate.id)
        .join(skill_counts, skill_counts.c.candidate_id == Candidate.id)
        .where(Candidate.primary_occupation_id.isnot(None))
        .order_by(Candidate.primary_occupation_id, Candidate.id)
    ).scalars().all()

    if len(eligible_ids) <= n:
        return list(eligible_ids)

    # Evenly-spaced stride over the occupation-sorted list, not a prefix --
    # e.g. with 2000 eligible and n=500, take every 4th one.
    stride = len(eligible_ids) / n
    return [eligible_ids[int(i * stride)] for i in range(n)]


def select_existing_golden_candidate_ids(session: Session) -> list[int]:
    """The canonical "who are the golden candidates, right now" lookup for
    every downstream golden-dataset v2 script (CVs, jobs, noise injection)
    -- unlike select_golden_candidate_ids above (which re-derives a
    selection from scratch every call), this reads back whoever
    select_and_assign_golden_candidates already assigned: candidates whose
    primary_occupation sits on one of the new curated, domain-tagged
    occupations. `Occupation.domain IS NOT NULL` is unambiguous for this --
    only the v2 curated occupations ever get a domain (see Occupation's
    docstring), so no stride/sampling logic is needed here at all.
    """
    return session.execute(
        select(Candidate.id)
        .join(Occupation, Occupation.id == Candidate.primary_occupation_id)
        .where(Occupation.domain.isnot(None))
        .order_by(Candidate.id)
    ).scalars().all()


def select_existing_golden_job_ids(session: Session) -> list[int]:
    """The job-side counterpart to select_existing_golden_candidate_ids --
    `golden_pairs` already is the explicit, queryable candidate->job
    mapping (app/golden/models.py's GoldenPair), so this is a direct read,
    no extraction-run dependency."""
    return session.execute(select(GoldenPair.job_id).order_by(GoldenPair.job_id)).scalars().all()


def select_and_assign_golden_candidates(session: Session, n: int = GOLDEN_V2_DEFAULT_N) -> list[int]:
    """Picks `n` candidates and deterministically reassigns each one to one
    of the new curated occupations (round-robin across all of them, for an
    even spread -- the same "deliberate, controlled diversity for later
    clustering" reasoning the original golden-dataset plan used for
    behavioral personas), regenerating their CandidateSkill/
    CandidateSkillAssessment rows to actually match. Idempotent to call
    again with a different `n`, but NOT incremental -- re-running deletes
    and rebuilds the skill rows for every candidate it selects each time,
    even ones selected on a prior call, since "regenerate to match the
    assigned occupation" is the whole point.
    """
    curated_occupations = {
        o.title: o for o in session.execute(
            select(Occupation).where(Occupation.title.in_(NEW_TITLES))
        ).scalars().all()
    }
    missing = [t for t in NEW_TITLES if t not in curated_occupations]
    if missing:
        raise RuntimeError(
            f"{len(missing)} curated occupations not found -- run "
            "`golden-curated-occupations` before golden candidate selection."
        )
    occupation_cycle = [curated_occupations[t] for t in NEW_TITLES]

    eligible_ids = session.execute(
        select(Candidate.id).where(Candidate.primary_occupation_id.isnot(None)).order_by(Candidate.id)
    ).scalars().all()
    if len(eligible_ids) < n:
        raise RuntimeError(f"Only {len(eligible_ids)} eligible candidates, need {n}.")
    stride = len(eligible_ids) / n
    selected_ids = [eligible_ids[int(i * stride)] for i in range(n)]

    all_skills = session.execute(select(Skill)).scalars().all()
    skills_by_domain = {
        domain: [s for s in all_skills if s.category in categories]
        for domain, categories in SKILL_DOMAINS.items()
    }

    rng = random.Random(GOLDEN_V2_SEED)
    for i, candidate_id in enumerate(selected_ids):
        occupation = occupation_cycle[i % len(occupation_cycle)]

        candidate = session.get(Candidate, candidate_id)
        candidate.primary_occupation_id = occupation.id

        session.query(CandidateSkillAssessment).filter_by(candidate_id=candidate_id).delete()
        session.query(CandidateSkill).filter_by(candidate_id=candidate_id).delete()

        k = rng.randint(4, 8)
        profile_skills = _assign_fixed_domain_skills(
            rng, all_skills, skills_by_domain[occupation.domain], k
        )
        seen_skill_ids = set()
        for sk in profile_skills:
            if sk.id in seen_skill_ids:
                continue
            seen_skill_ids.add(sk.id)
            session.add(CandidateSkill(
                candidate_id=candidate_id, skill_id=sk.id,
                proficiency=rng.choice(list(ProficiencyLevel)),
                evidence_source=rng.choice(list(EvidenceSource)),
            ))

        if rng.random() < 0.2:
            unclaimed = [s for s in all_skills if s.id not in seen_skill_ids]
            if unclaimed:
                assessed_skill = rng.choice(unclaimed)
                session.add(CandidateSkillAssessment(
                    candidate_id=candidate_id, skill_id=assessed_skill.id,
                    result=rng.choice(list(AssessmentResult)),
                    assessed_at=datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 180)),
                ))

        if (i + 1) % 100 == 0:
            session.commit()
    session.commit()

    return selected_ids


if __name__ == "__main__":
    import argparse

    from app.common.logging import setup_logging
    from app.db import SessionLocal

    setup_logging("golden-assign-candidates")
    parser = argparse.ArgumentParser(description="Select and assign golden candidates (golden-dataset v2).")
    parser.add_argument("--n", type=int, default=GOLDEN_V2_DEFAULT_N)
    args = parser.parse_args()

    _session = SessionLocal()
    try:
        _ids = select_and_assign_golden_candidates(_session, n=args.n)
        print(f"Assigned {len(_ids)} golden candidates to curated occupations.")
    finally:
        _session.close()
