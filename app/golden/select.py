"""Deterministic selection of the 500 "golden" candidates -- a pure function
over already-seeded data, not a stored selection, so every golden-dataset
script (CVs, jobs, logins, persona events, eval harnesses) re-derives the
exact same 500 ids without needing a manifest file or extra table.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.source_models import Candidate, CandidateSkill

DEFAULT_N = 500
MIN_SKILLS = 3


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
