"""Hard eligibility constraints — evaluated separately from, and before, the
soft ranking score in scoring.py.

Per the brief: "Do not allow a strong semantic similarity score to override a
legally or operationally required eligibility constraint." This project has
no real legal/licensing data (synthetic dataset, no such fields), so the one
constraint implemented here is a deliberately conservative, documented
placeholder — not a claim of real-world completeness. A production system
would add constraints like work authorization or required licensure here,
never as part of the weighted score.
"""
from dataclasses import dataclass

from app.matching.profiles import CandidateProfile, JobProfile


@dataclass
class EligibilityResult:
    """Attributes:
    eligible: whether the candidate passes every hard constraint.
    reason: a human-readable explanation when `eligible` is `False`;
        `None` when `eligible` is `True`.
    """

    eligible: bool
    reason: str | None = None


def check_eligibility(candidate: CandidateProfile, job: JobProfile) -> EligibilityResult:
    """Checks hard pass/fail constraints for one candidate/job pair —
    always run before scoring, never folded into the weighted score (see
    module docstring for why).

    Args:
        candidate: the candidate's profile.
        job: the job's profile.

    Returns:
        EligibilityResult: `eligible=False` if the job has required skills
            and the candidate matches none of them; `eligible=True`
            otherwise (including when the job has no required skills).
    """
    if job.required_skill_ids and not (candidate.skill_ids & job.required_skill_ids):
        return EligibilityResult(
            eligible=False,
            reason="Candidate has none of the job's required skills",
        )
    return EligibilityResult(eligible=True)
