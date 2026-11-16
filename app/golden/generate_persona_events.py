"""Persona-driven behavioral history for the golden candidates --
deliberately controlled activity patterns (not the generic
app/analytics/generate_synthetic_events.py's uniform-random session
assignment), so a later clustering pass has real, separable behavioral
structure to find alongside the skill/occupation axis.

Critically, this **backdates `received_at`**, not just `occurred_at` --
app/analytics/segmentation.py's recency feature is computed from
`received_at` (the "server-set, authoritative for ordering" column), which
the generic generator always sets to `datetime.now()`. Without backdating
it, every candidate looks equally "just active" regardless of assigned
persona, and the existing GET /actors/candidate/{id}/segment endpoint can
never return anything but a volume-driven label. Grounded directly in
segmentation.py's real thresholds (ACTIVE_EVENT_COUNT_THRESHOLD=5,
ACTIVE_CONVERSION_THRESHOLD=0.5, DORMANT_DAYS_THRESHOLD=30,
FREQUENCY_WINDOW_DAYS=30).

Run with: docker compose run --rm golden-events [--limit N]

Not idempotent in the per-row sense (events have no natural unique key) --
re-running appends more events for the same personas rather than erroring,
consistent with the generic generator's own one-shot-per-invocation model.
Re-running is still *safe* (never corrupts existing data), just additive.
"""
import argparse
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.analytics.models import BehavioralEvent
from app.db import SessionLocal
from app.golden.models import GoldenPair
from app.golden.select import select_existing_golden_candidate_ids
from app.ingestion.source_models import Job

SEED = 1337

PERSONA_POWER_APPLIER = "power_applier"
PERSONA_ACTIVE_BROWSER = "active_browser"
PERSONA_PASSIVE_SEEKER = "passive_seeker"
PERSONA_DORMANT_RECENT = "dormant_recent"
PERSONA_DORMANT_NEVER_ENGAGED = "dormant_never_engaged"

# Skewed, not uniform -- see the plan's rationale: a realistic engagement
# distribution gives a later business-insights narrative something to say
# ("only 15% of candidates are highly engaged").
PERSONA_WEIGHTS = {
    PERSONA_POWER_APPLIER: 0.15,
    PERSONA_ACTIVE_BROWSER: 0.20,
    PERSONA_PASSIVE_SEEKER: 0.30,
    PERSONA_DORMANT_RECENT: 0.20,
    PERSONA_DORMANT_NEVER_ENGAGED: 0.15,
}


def assign_personas(candidate_ids: list[int], seed: int = SEED) -> dict[int, str]:
    """Deterministic, independent of occupation/selection order -- the
    input list is shuffled with a fixed seed before slicing into persona
    buckets, so persona assignment doesn't correlate with
    select_existing_golden_candidate_ids' id-sorted ordering (or, before
    golden-dataset v2, select_golden_candidate_ids' occupation-sorted one).
    """
    rng = random.Random(seed)
    shuffled = list(candidate_ids)
    rng.shuffle(shuffled)

    assignment: dict[int, str] = {}
    start = 0
    personas = list(PERSONA_WEIGHTS.items())
    for idx, (persona, weight) in enumerate(personas):
        if idx == len(personas) - 1:
            end = len(shuffled)
        else:
            end = start + round(weight * len(candidate_ids))
        for candidate_id in shuffled[start:end]:
            assignment[candidate_id] = persona
        start = end
    return assignment


def _event(*, actor_id, event_type, entity_id, received_at, session_id, metadata=None) -> BehavioralEvent:
    return BehavioralEvent(
        event_id=str(uuid.uuid4()),
        occurred_at=received_at,
        received_at=received_at,
        actor_type="candidate",
        actor_id=actor_id,
        session_id=session_id,
        event_type=event_type,
        entity_type="job" if entity_id is not None else None,
        entity_id=entity_id,
        source="golden_persona",
        metadata_=metadata or {},
    )


def _pick_shown_jobs(
    rng: random.Random, golden_job_ids: list[int], pool_job_ids: list[int], n: int
) -> list[int]:
    """Mixes in the candidate's own golden (near-perfect-match) jobs when
    available, plus random pool jobs -- realistic "sees some great matches,
    some unrelated postings too" variety, not every impression a perfect fit.
    """
    shown = list(golden_job_ids)
    remaining = max(0, n - len(shown))
    if remaining and pool_job_ids:
        shown += rng.sample(pool_job_ids, k=min(remaining, len(pool_job_ids)))
    rng.shuffle(shown)
    return shown[:n]


def _generate_session(
    rng, actor_id, golden_job_ids, pool_job_ids, received_at, n_shown, click_rate, apply_given_click_rate,
) -> list[BehavioralEvent]:
    session_id = str(uuid.uuid4())
    events = [_event(
        actor_id=actor_id, event_type="login", entity_id=None,
        received_at=received_at, session_id=session_id,
    )]

    shown_jobs = _pick_shown_jobs(rng, golden_job_ids, pool_job_ids, n_shown)
    for rank, job_id in enumerate(shown_jobs, start=1):
        events.append(_event(
            actor_id=actor_id, event_type="recommendation_shown", entity_id=job_id,
            received_at=received_at, session_id=session_id, metadata={"rank": rank},
        ))
        if rng.random() < click_rate:
            click_at = received_at + timedelta(seconds=rng.randint(5, 600))
            events.append(_event(
                actor_id=actor_id, event_type="recommendation_clicked", entity_id=job_id,
                received_at=click_at, session_id=session_id, metadata={"rank": rank},
            ))
            if rng.random() < apply_given_click_rate:
                events.append(_event(
                    actor_id=actor_id, event_type="job_applied", entity_id=job_id,
                    received_at=click_at + timedelta(seconds=rng.randint(30, 900)), session_id=session_id,
                ))
    return events


def _events_for_persona(rng, persona, actor_id, golden_job_ids, pool_job_ids, now) -> list[BehavioralEvent]:
    events: list[BehavioralEvent] = []

    if persona == PERSONA_DORMANT_NEVER_ENGAGED:
        if rng.random() < 0.5:
            return []
        received_at = now - timedelta(days=rng.randint(100, 150))
        return [_event(
            actor_id=actor_id, event_type="login", entity_id=None,
            received_at=received_at, session_id=str(uuid.uuid4()),
        )]

    if persona == PERSONA_DORMANT_RECENT:
        # A real burst of activity 45-90 days ago, then nothing since --
        # days_since_last_event > DORMANT_DAYS_THRESHOLD (30).
        n_sessions = rng.randint(3, 5)
        for _ in range(n_sessions):
            received_at = now - timedelta(days=rng.randint(45, 90), minutes=rng.randint(0, 1440))
            events += _generate_session(
                rng, actor_id, golden_job_ids, pool_job_ids, received_at,
                n_shown=rng.randint(3, 5), click_rate=0.3, apply_given_click_rate=0.2,
            )
        return events

    if persona == PERSONA_PASSIVE_SEEKER:
        # Deliberately low volume -- exactly 1 session, 2 shown impressions,
        # a low click rate: login+shown(2)+click(0-1) stays safely under
        # ACTIVE_EVENT_COUNT_THRESHOLD (5) within the 30-day window. Two
        # sessions (as originally tried) occasionally tipped over 5 and
        # got misclassified as active_seeker -- confirmed by checking real
        # segment_actor() output against the assigned persona, not assumed.
        # apply_given_click_rate=0.0, deliberately -- a passive_seeker who
        # applies would also push conversion_rate (applied/shown) up to or
        # past ACTIVE_CONVERSION_THRESHOLD (0.5) on this persona's tiny
        # shown-count, flipping it to active_seeker (confirmed happening in
        # ~2/25 of an earlier test run before this was pinned to 0).
        received_at = now - timedelta(days=rng.randint(1, 25), minutes=rng.randint(0, 1440))
        events += _generate_session(
            rng, actor_id, golden_job_ids, pool_job_ids, received_at,
            n_shown=2, click_rate=0.15, apply_given_click_rate=0.0,
        )
        return events

    if persona == PERSONA_ACTIVE_BROWSER:
        # High volume, low conversion -- active via event_count_window >= 5,
        # not via conversion_rate.
        n_sessions = rng.randint(6, 10)
        for _ in range(n_sessions):
            received_at = now - timedelta(days=rng.randint(1, 25), minutes=rng.randint(0, 1440))
            events += _generate_session(
                rng, actor_id, golden_job_ids, pool_job_ids, received_at,
                n_shown=rng.randint(5, 8), click_rate=0.2, apply_given_click_rate=0.08,
            )
        return events

    if persona == PERSONA_POWER_APPLIER:
        # Active via conversion_rate >= 0.5 (applied/shown) -- high
        # click-through AND a high apply-given-click rate.
        n_sessions = rng.randint(4, 8)
        for _ in range(n_sessions):
            received_at = now - timedelta(days=rng.randint(1, 25), minutes=rng.randint(0, 1440))
            events += _generate_session(
                rng, actor_id, golden_job_ids, pool_job_ids, received_at,
                n_shown=rng.randint(3, 5), click_rate=0.7, apply_given_click_rate=0.6,
            )
        return events

    raise ValueError(f"Unknown persona: {persona}")


def main(limit: int | None) -> None:
    session = SessionLocal()
    try:
        golden_ids = select_existing_golden_candidate_ids(session)
        if limit is not None:
            golden_ids = golden_ids[:limit]

        personas = assign_personas(golden_ids)
        pool_job_ids = session.execute(select(Job.id)).scalars().all()

        golden_jobs_by_candidate: dict[int, list[int]] = {}
        rows = session.execute(
            select(GoldenPair.candidate_id, GoldenPair.job_id).where(GoldenPair.candidate_id.in_(golden_ids))
        ).all()
        for candidate_id, job_id in rows:
            golden_jobs_by_candidate.setdefault(candidate_id, []).append(job_id)

        rng = random.Random(SEED)
        now = datetime.now(timezone.utc)
        total_events = 0
        persona_counts: dict[str, int] = {}

        for i, candidate_id in enumerate(golden_ids, start=1):
            persona = personas[candidate_id]
            persona_counts[persona] = persona_counts.get(persona, 0) + 1
            events = _events_for_persona(
                rng, persona, candidate_id,
                golden_jobs_by_candidate.get(candidate_id, []), pool_job_ids, now,
            )
            session.add_all(events)
            total_events += len(events)
            if i % 50 == 0:
                session.commit()
                print(f"[{i}/{len(golden_ids)}] committed so far, {total_events} events total")

        session.commit()
        print(f"Golden persona events generated: {total_events} events across {len(golden_ids)} candidates")
        print(f"Persona distribution: {persona_counts}")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N golden candidates")
    args = parser.parse_args()
    main(limit=args.limit)
