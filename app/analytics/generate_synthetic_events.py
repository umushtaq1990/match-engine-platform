"""Deterministic synthetic behavioral-event generator (Phase 14).

Run with: python -m app.analytics.generate_synthetic_events
(after `migrate` and `seed` have already run — this references their ids)

Simulates what a real frontend would eventually report (clicks, applies,
saves, logins) plus the `recommendation_shown` impressions the matching API
auto-logs, against the already-seeded candidate/job/course ids. Deterministic
(fixed seed), with a realistic drop-off funnel rather than a 1:1:1 pipeline:
most impressions get no follow-up, a smaller share get clicked, a smaller
share still of those get applied — see docs/behavioral-analytics.md.

No employer-user table exists yet in this domain model (jobs only have a
`company_name` string, not an Employer entity). For employer-side events
(viewing candidate matches for a job), job_id stands in for the acting
employer's identity until a real Employer entity exists — a documented v1
limitation, not an oversight (see docs/progress.md's domain-model
genericization note).
"""
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.analytics.models import BehavioralEvent
from app.db import SessionLocal

SEED = 42
N_CANDIDATE_SESSIONS = 1500
N_EMPLOYER_SESSIONS = 300
TOP_K = 10
CLICK_RATE = 0.25
APPLY_RATE_GIVEN_CLICK = 0.30
SAVE_RATE_GIVEN_CLICK_NO_APPLY = 0.40
NO_SESSION_ID_RATE = 0.10
DUPLICATE_RATE = 0.02
ALGORITHM_VERSION = "rule-based-v1"


def _event(
    *, actor_type, actor_id, event_type, entity_type=None, entity_id=None,
    session_id=None, occurred_at=None, metadata=None,
) -> BehavioralEvent:
    return BehavioralEvent(
        event_id=str(uuid.uuid4()),
        occurred_at=occurred_at,
        received_at=datetime.now(timezone.utc),
        actor_type=actor_type,
        actor_id=actor_id,
        session_id=session_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        source="synthetic",
        metadata_=metadata or {},
    )


def generate() -> int:
    rng = random.Random(SEED)
    session = SessionLocal()
    try:
        candidate_ids = [row[0] for row in session.execute(text("SELECT id FROM candidates"))]
        job_ids = [row[0] for row in session.execute(text("SELECT id FROM jobs"))]
        course_ids = [row[0] for row in session.execute(text("SELECT id FROM courses"))]

        events: list[BehavioralEvent] = []

        for _ in range(N_CANDIDATE_SESSIONS):
            candidate_id = rng.choice(candidate_ids)
            session_id = None if rng.random() < NO_SESSION_ID_RATE else str(uuid.uuid4())
            occurred_at = datetime.now(timezone.utc) - timedelta(
                days=rng.randint(0, 60), minutes=rng.randint(0, 1440)
            )

            events.append(_event(
                actor_type="candidate", actor_id=candidate_id, event_type="login",
                session_id=session_id, occurred_at=occurred_at,
            ))

            shown_jobs = rng.sample(job_ids, k=min(TOP_K, len(job_ids)))
            for rank, job_id in enumerate(shown_jobs, start=1):
                events.append(_event(
                    actor_type="candidate", actor_id=candidate_id, event_type="recommendation_shown",
                    entity_type="job", entity_id=job_id, session_id=session_id, occurred_at=occurred_at,
                    metadata={"rank": rank, "score": round(rng.uniform(0.3, 0.95), 2),
                              "algorithm_version": ALGORITHM_VERSION},
                ))
                if rng.random() < CLICK_RATE:
                    click_at = occurred_at + timedelta(seconds=rng.randint(5, 600))
                    events.append(_event(
                        actor_type="candidate", actor_id=candidate_id,
                        event_type="recommendation_clicked", entity_type="job", entity_id=job_id,
                        session_id=session_id, occurred_at=click_at, metadata={"rank": rank},
                    ))
                    if rng.random() < APPLY_RATE_GIVEN_CLICK:
                        events.append(_event(
                            actor_type="candidate", actor_id=candidate_id, event_type="job_applied",
                            entity_type="job", entity_id=job_id, session_id=session_id,
                            occurred_at=click_at + timedelta(seconds=rng.randint(30, 900)),
                        ))
                    elif rng.random() < SAVE_RATE_GIVEN_CLICK_NO_APPLY:
                        events.append(_event(
                            actor_type="candidate", actor_id=candidate_id, event_type="job_saved",
                            entity_type="job", entity_id=job_id, session_id=session_id,
                            occurred_at=click_at + timedelta(seconds=rng.randint(5, 120)),
                        ))

            if course_ids and rng.random() < 0.3:
                shown_courses = rng.sample(course_ids, k=min(5, len(course_ids)))
                for rank, course_id in enumerate(shown_courses, start=1):
                    events.append(_event(
                        actor_type="candidate", actor_id=candidate_id,
                        event_type="recommendation_shown", entity_type="course", entity_id=course_id,
                        session_id=session_id, occurred_at=occurred_at,
                        metadata={"rank": rank, "score": round(rng.uniform(0.2, 0.9), 2),
                                  "algorithm_version": ALGORITHM_VERSION},
                    ))

        # Employer side: job_id stands in for the acting employer (see module docstring).
        for job_id in rng.sample(job_ids, k=min(N_EMPLOYER_SESSIONS, len(job_ids))):
            occurred_at = datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 60))
            shown_candidates = rng.sample(candidate_ids, k=min(TOP_K, len(candidate_ids)))
            for rank, cand_id in enumerate(shown_candidates, start=1):
                events.append(_event(
                    actor_type="employer", actor_id=job_id, event_type="recommendation_shown",
                    entity_type="candidate", entity_id=cand_id, occurred_at=occurred_at,
                    metadata={"rank": rank, "score": round(rng.uniform(0.3, 0.95), 2),
                              "algorithm_version": ALGORITHM_VERSION},
                ))

        # Deliberate duplicates — same actor+entity+type, re-sent moments later —
        # so dedup is something downstream analysis has to actually handle.
        for source_event in [e for e in list(events) if rng.random() < DUPLICATE_RATE]:
            events.append(_event(
                actor_type=source_event.actor_type, actor_id=source_event.actor_id,
                event_type=source_event.event_type, entity_type=source_event.entity_type,
                entity_id=source_event.entity_id, session_id=source_event.session_id,
                occurred_at=source_event.occurred_at, metadata=source_event.metadata_,
            ))

        session.add_all(events)
        session.commit()
        return len(events)
    finally:
        session.close()


if __name__ == "__main__":
    n = generate()
    print(f"Generated {n} synthetic behavioral events.")
