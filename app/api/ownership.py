"""Ownership checks (issue #26) -- closes the gap issue #10 originally
flagged ("anyone logged in can look up any candidate_id/job_id"). An
unowned record (candidate/job rows from the 1000+2000-row seeded synthetic
dataset, never linked to a real Keycloak identity) stays freely accessible
on read-only "lenient" routes, preserving existing demo behavior against
that dataset; write/private routes use `allow_unowned=False` so nobody can
edit or read private data on a record nobody actually owns.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.identity.models import UserProfile
from app.ingestion.source_models import Job


def candidate_owner_sub(db: Session, candidate_id: int) -> str | None:
    profile = db.query(UserProfile).filter_by(candidate_id=candidate_id).first()
    return profile.keycloak_user_id if profile is not None else None


def job_owner_sub(db: Session, job_id: int) -> str | None:
    job = db.get(Job, job_id)
    return job.posted_by_keycloak_user_id if job is not None else None


def require_owner(owner_sub: str | None, caller_sub: str, allow_unowned: bool) -> None:
    if owner_sub is None:
        if allow_unowned:
            return
        raise HTTPException(status_code=403, detail="No linked record for this account")
    if owner_sub != caller_sub:
        raise HTTPException(status_code=403, detail="You do not have access to this record")
