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
    eligible: bool
    reason: str | None = None


def check_eligibility(candidate: CandidateProfile, job: JobProfile) -> EligibilityResult:
    if job.required_skill_ids and not (candidate.skill_ids & job.required_skill_ids):
        return EligibilityResult(
            eligible=False,
            reason="Candidate has none of the job's required skills",
        )
    return EligibilityResult(eligible=True)
