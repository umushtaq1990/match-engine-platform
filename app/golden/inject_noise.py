"""golden-dataset v2: deliberately corrupts ~10% of golden candidates' and
~10% of golden jobs' ground truth with 1-2 unrelated skills, AFTER their
CV/job text is already written (never regenerated afterward) -- the
injected skill is picked from a domain unrelated to that example's own
occupation, so it's guaranteed not to appear anywhere in the text by
construction. Every injection is recorded in `golden_injected_noise`
(app/golden/models.py) so a LATER pass (not built here, per the plan this
shipped from) can check whether extraction, or the confidence-scoring
review queue (app/extraction/pipeline.py's _document_confidence), actually
flags these as data-quality issues.

Run with: docker compose run --rm golden-inject-noise [--rate 0.1]

Idempotent: skips any candidate/job that already has a golden_injected_noise
row -- re-running tops up to the target rate rather than re-rolling
everyone (so repeated runs don't keep silently corrupting more and more of
the dataset).
"""
import argparse
import random
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.golden.models import GoldenInjectedNoise, GoldenPair
from app.golden.select import select_existing_golden_candidate_ids
from app.ingestion.generate_synthetic_data import SKILL_DOMAINS
from app.ingestion.source_models import (
    Candidate,
    CandidateSkill,
    EvidenceSource,
    Job,
    JobSkill,
    Occupation,
    ProficiencyLevel,
    RequirementLevel,
    Skill,
)

DEFAULT_RATE = 0.10
SEED = 13131


def _skills_by_domain(session) -> dict[str, list[Skill]]:
    all_skills = session.execute(select(Skill)).scalars().all()
    return {
        domain: [s for s in all_skills if s.category in categories]
        for domain, categories in SKILL_DOMAINS.items()
    }


def _pick_unrelated_skill(rng, skills_by_domain, own_domain: str, exclude_ids: set[int]) -> Skill | None:
    """Excludes skill ids the example already has (either from its real
    profile or an earlier noise skill injected in the same call) -- found
    live: without this, a candidate who already happens to hold one of the
    "unrelated domain" skills as part of their own normal cross-domain
    noise (the ~20-30% _assign_fixed_domain_skills mixes in) collided with
    a candidate_skills/job_skills unique constraint. Returns None if every
    skill in every other domain is somehow already excluded (should not
    happen in practice given domain pool sizes)."""
    other_domains = [d for d in skills_by_domain if d != own_domain and skills_by_domain[d]]
    rng.shuffle(other_domains)
    for domain in other_domains:
        candidates = [s for s in skills_by_domain[domain] if s.id not in exclude_ids]
        if candidates:
            return rng.choice(candidates)
    return None


def _inject_candidate_noise(session, rng, skills_by_domain, candidate_ids: list[int], rate: float) -> int:
    already_noised = {
        row[0] for row in session.execute(
            select(GoldenInjectedNoise.candidate_id).where(GoldenInjectedNoise.candidate_id.isnot(None))
        ).all()
    }
    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    candidates = {c.id: c for c in session.execute(
        select(Candidate).where(Candidate.id.in_(candidate_ids))
    ).scalars().all()}
    existing_skills_by_candidate: dict[int, set[int]] = {}
    for cid, skill_id in session.execute(
        select(CandidateSkill.candidate_id, CandidateSkill.skill_id).where(
            CandidateSkill.candidate_id.in_(candidate_ids)
        )
    ).all():
        existing_skills_by_candidate.setdefault(cid, set()).add(skill_id)

    target_count = round(len(candidate_ids) * rate)
    eligible = [cid for cid in candidate_ids if cid not in already_noised]
    chosen = rng.sample(eligible, k=min(target_count, len(eligible)))

    injected = 0
    for candidate_id in chosen:
        own_domain = occupation_domain.get(candidates[candidate_id].primary_occupation_id)
        if own_domain is None:
            continue
        exclude_ids = set(existing_skills_by_candidate.get(candidate_id, set()))
        n_skills = rng.randint(1, 2)
        for _ in range(n_skills):
            skill = _pick_unrelated_skill(rng, skills_by_domain, own_domain, exclude_ids)
            if skill is None:
                break
            exclude_ids.add(skill.id)
            session.add(CandidateSkill(
                candidate_id=candidate_id, skill_id=skill.id,
                proficiency=ProficiencyLevel.intermediate, evidence_source=EvidenceSource.self_reported,
            ))
            session.add(GoldenInjectedNoise(
                candidate_id=candidate_id, skill_id=skill.id, injected_at=datetime.now(timezone.utc),
            ))
        injected += 1
    session.commit()
    return injected


def _inject_job_noise(session, rng, skills_by_domain, candidate_ids: list[int], rate: float) -> int:
    already_noised = {
        row[0] for row in session.execute(
            select(GoldenInjectedNoise.job_id).where(GoldenInjectedNoise.job_id.isnot(None))
        ).all()
    }
    job_ids = session.execute(
        select(GoldenPair.job_id).where(GoldenPair.candidate_id.in_(candidate_ids))
    ).scalars().all()
    jobs = {j.id: j for j in session.execute(select(Job).where(Job.id.in_(job_ids))).scalars().all()}
    occupation_domain = dict(session.execute(select(Occupation.id, Occupation.domain)).all())
    existing_skills_by_job: dict[int, set[int]] = {}
    for jid, skill_id in session.execute(
        select(JobSkill.job_id, JobSkill.skill_id).where(JobSkill.job_id.in_(job_ids))
    ).all():
        existing_skills_by_job.setdefault(jid, set()).add(skill_id)

    target_count = round(len(job_ids) * rate)
    eligible = [jid for jid in job_ids if jid not in already_noised]
    chosen = rng.sample(eligible, k=min(target_count, len(eligible)))

    injected = 0
    for job_id in chosen:
        own_domain = occupation_domain.get(jobs[job_id].occupation_id)
        if own_domain is None:
            continue
        exclude_ids = set(existing_skills_by_job.get(job_id, set()))
        n_skills = rng.randint(1, 2)
        for _ in range(n_skills):
            skill = _pick_unrelated_skill(rng, skills_by_domain, own_domain, exclude_ids)
            if skill is None:
                break
            exclude_ids.add(skill.id)
            session.add(JobSkill(
                job_id=job_id, skill_id=skill.id, requirement_level=RequirementLevel.required,
            ))
            session.add(GoldenInjectedNoise(
                job_id=job_id, skill_id=skill.id, injected_at=datetime.now(timezone.utc),
            ))
        injected += 1
    session.commit()
    return injected


def main(rate: float) -> None:
    session = SessionLocal()
    try:
        candidate_ids = select_existing_golden_candidate_ids(session)
        skills_by_domain = _skills_by_domain(session)
        rng = random.Random(SEED)

        candidates_injected = _inject_candidate_noise(session, rng, skills_by_domain, candidate_ids, rate)
        jobs_injected = _inject_job_noise(session, rng, skills_by_domain, candidate_ids, rate)

        print(f"Candidates with injected noise: {candidates_injected} (target rate {rate:.0%})")
        print(f"Jobs with injected noise:       {jobs_injected} (target rate {rate:.0%})")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE)
    args = parser.parse_args()
    main(rate=args.rate)
