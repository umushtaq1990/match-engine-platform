"""Batch computation/refresh of entity embeddings (Phase 17).

Run with: python -m app.embeddings.compute_embeddings
  [--embedding-model X] [--candidate-limit N] [--job-limit N]

Incremental, not a blanket recompute: each dimension's source_hash is
checked against the stored row first, and only a drifted/missing hash
triggers a real embedding call — see docs/embedding-matching.md
"Invalidation". The full population (2,000 candidates + 1,000 jobs, two
dimensions each needing a real LLM call) is a real but non-trivial amount of
wall-clock time; `--candidate-limit`/`--job-limit` let a smaller, faster
pass verify the pipeline end-to-end without waiting for the whole dataset —
the same "sample first, scale later" judgment call Phase 4 made before
Phase 9's N+1 fix made full-population evaluation cheap.
"""
import argparse
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.embeddings.cache_invalidation import invalidate_candidate_cache
from app.embeddings.hashing import DIMENSION_HASHERS
from app.embeddings.models import EntityEmbedding
from app.embeddings.pipeline import compute_embedding
from app.ingestion.source_models import Candidate, Job

DEFAULT_EMBEDDING_MODEL = "all-minilm"
DIMENSIONS = ["skills", "experience", "overall"]
COMMIT_EVERY = 50


def refresh_entity(
    session: Session, entity_type: str, entity_id: int, embedding_model: str
) -> dict[str, int]:
    stats = {"computed": 0, "skipped": 0, "no_data": 0}
    for dimension in DIMENSIONS:
        current_hash = DIMENSION_HASHERS[dimension](session, entity_type, entity_id)
        if current_hash is None:
            stats["no_data"] += 1
            continue

        existing = session.execute(
            select(EntityEmbedding).where(
                EntityEmbedding.entity_type == entity_type,
                EntityEmbedding.entity_id == entity_id,
                EntityEmbedding.dimension == dimension,
                EntityEmbedding.embedding_model == embedding_model,
            )
        ).scalar_one_or_none()

        if existing is not None and existing.source_hash == current_hash:
            stats["skipped"] += 1
            continue

        vector = compute_embedding(session, entity_type, entity_id, dimension, embedding_model)
        if vector is None:
            stats["no_data"] += 1
            continue

        if existing is not None:
            existing.vector = vector
            existing.source_hash = current_hash
            existing.computed_at = datetime.now(timezone.utc)
        else:
            session.add(EntityEmbedding(
                entity_type=entity_type, entity_id=entity_id, dimension=dimension,
                embedding_model=embedding_model, source_hash=current_hash, vector=vector,
                computed_at=datetime.now(timezone.utc),
            ))
        stats["computed"] += 1

    # Phase 25: a candidate's job-matches cache (app/api/cache.py) depends
    # directly on their own embeddings existing/being current — the default
    # ranking mode (Phase 22) silently falls back to rule-based otherwise.
    # Without this, a candidate whose embeddings just went from "missing" to
    # "present" could keep serving a stale "rule_based_no_embeddings" result
    # for up to the full 1-hour TTL. Jobs aren't invalidated here: a job's
    # own embedding changing doesn't have a traceable "which candidates
    # cached this job" index, a real, documented v3 gap, not an oversight.
    if entity_type == "candidate" and stats["computed"] > 0:
        invalidate_candidate_cache(entity_id)
    return stats


def _merge(totals: dict[str, int], stats: dict[str, int]) -> None:
    for key, value in stats.items():
        totals[key] += value


def refresh_all(
    session: Session,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    candidate_limit: int | None = None,
    job_limit: int | None = None,
) -> dict[str, int]:
    totals = {"computed": 0, "skipped": 0, "no_data": 0}

    candidate_stmt = select(Candidate.id).order_by(Candidate.id)
    if candidate_limit is not None:
        candidate_stmt = candidate_stmt.limit(candidate_limit)
    candidate_ids = session.execute(candidate_stmt).scalars().all()

    for i, candidate_id in enumerate(candidate_ids):
        _merge(totals, refresh_entity(session, "candidate", candidate_id, embedding_model))
        if (i + 1) % COMMIT_EVERY == 0:
            session.commit()
    session.commit()

    job_stmt = select(Job.id).order_by(Job.id)
    if job_limit is not None:
        job_stmt = job_stmt.limit(job_limit)
    job_ids = session.execute(job_stmt).scalars().all()

    for i, job_id in enumerate(job_ids):
        _merge(totals, refresh_entity(session, "job", job_id, embedding_model))
        if (i + 1) % COMMIT_EVERY == 0:
            session.commit()
    session.commit()

    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--candidate-limit", type=int, default=None)
    parser.add_argument("--job-limit", type=int, default=None)
    args = parser.parse_args()

    db_session = SessionLocal()
    totals = refresh_all(
        db_session,
        embedding_model=args.embedding_model,
        candidate_limit=args.candidate_limit,
        job_limit=args.job_limit,
    )
    print(f"Embeddings refreshed (model={args.embedding_model}): {totals}")
    db_session.close()
