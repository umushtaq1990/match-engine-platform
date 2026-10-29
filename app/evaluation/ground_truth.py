"""Synthetic ground-truth relevance labels for evaluating candidate-job
ranking quality.

Per the brief: "Use realistic relevance labels where possible, while clearly
documenting that synthetic labels do not prove real-world effectiveness."

Definition used here: a job is "relevant" to a candidate if the job's
occupation matches the candidate's stated target occupation
(Candidate.primary_occupation_id == Job.occupation_id). This is deliberately
INDEPENDENT of the scoring formula being evaluated (scoring.py blends skill/
location/salary/occupation; ground truth here uses only occupation), so
measuring the ranking against it is meaningful rather than circular — we are
testing whether the overall ranking surfaces occupation-aligned jobs near
the top, not just re-deriving the same formula's own output.

This is a judgment call, not a validated real-world truth: it assumes any
job in a candidate's target occupation is a reasonable match candidate,
which is a coarser notion of "relevant" than a real recruiter would use (it
ignores skill gaps entirely — that's what skill_gap.py analyzes separately).
Documented limitation, not hidden.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.source_models import Candidate, Job


def build_ground_truth(session: Session) -> dict[int, set[int]]:
    """candidate_id -> set of relevant job_ids.

    Excludes candidates with no primary_occupation_id (no target occupation
    to define relevance against) and candidates whose target occupation has
    zero posted jobs (no possible ground truth). Both exclusions are
    legitimate dataset characteristics, not errors — counted and reported by
    the caller, not silently dropped.
    """
    jobs_by_occupation: dict[int, set[int]] = {}
    for job_id, occupation_id in session.execute(select(Job.id, Job.occupation_id)).all():
        if occupation_id is not None:
            jobs_by_occupation.setdefault(occupation_id, set()).add(job_id)

    ground_truth: dict[int, set[int]] = {}
    candidates = session.execute(
        select(Candidate.id, Candidate.primary_occupation_id).where(
            Candidate.primary_occupation_id.isnot(None)
        )
    ).all()
    for candidate_id, occupation_id in candidates:
        relevant = jobs_by_occupation.get(occupation_id)
        if relevant:
            ground_truth[candidate_id] = relevant

    return ground_truth
