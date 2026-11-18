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

Entity types are registered in `ENTITY_ID_QUERIES`/`ENTITY_CACHE_INVALIDATORS`
below rather than hardcoded as two parallel candidate/job code blocks —
`refresh_all` loops over whatever entity types are registered, so adding a
third entity type elsewhere is two dict entries here, not a new copy of
the loop body.
"""
import argparse
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.embeddings.cache_invalidation import invalidate_candidate_cache
from app.embeddings.hashing import DIMENSION_HASHERS
from app.embeddings.models import EntityEmbedding
from app.embeddings.pipeline import compute_embedding
from app.ingestion.source_models import Candidate, Job

DEFAULT_EMBEDDING_MODEL = "all-minilm"
COMMIT_EVERY = 50

# Every entity type this pipeline knows how to walk in bulk, and the query
# that yields all of its ids. `refresh_entity` (below) doesn't need this —
# it already knows which single entity to refresh — only `refresh_all`'s
# "walk everything" loop does.
ENTITY_ID_QUERIES: dict[str, Select] = {
    "candidate": select(Candidate.id).order_by(Candidate.id),
    "job": select(Job.id).order_by(Job.id),
}

# Only candidates have a cache that needs invalidating when their
# embeddings change (see refresh_entity's docstring for why) -- jobs have
# no entry here deliberately, not as an oversight.
ENTITY_CACHE_INVALIDATORS: dict[str, Callable[[int], None]] = {
    "candidate": invalidate_candidate_cache,
}


def refresh_entity(
    session: Session, entity_type: str, entity_id: int, embedding_model: str
) -> dict[str, int]:
    """Recomputes every stale/missing embedding dimension for one entity.

    Each dimension in `DIMENSION_HASHERS` is checked independently: a
    dimension whose current source hash matches the stored row is left
    alone (`"skipped"`), one with no source data at all is counted as
    `"no_data"` (e.g. a candidate with zero skills), and everything else
    triggers a real `compute_embedding` call (`"computed"`).

    Args:
        session: an active SQLAlchemy session. Callers commit; this
            function only adds/mutates in-session state.
        entity_type: `"candidate"` or `"job"`.
        entity_id: the candidate's or job's primary key.
        embedding_model: the model name recorded on `EntityEmbedding` rows
            and passed through to `compute_embedding`.

    Returns:
        dict[str, int]: `{"computed": n, "skipped": n, "no_data": n}` —
            per-dimension outcome counts, each in `[0, len(DIMENSION_HASHERS)]`.
    """
    stats = {"computed": 0, "skipped": 0, "no_data": 0}
    for dimension in DIMENSION_HASHERS:
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
    invalidate = ENTITY_CACHE_INVALIDATORS.get(entity_type)
    if invalidate is not None and stats["computed"] > 0:
        invalidate(entity_id)
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
    """Refreshes every registered entity type's embeddings, committing
    every `COMMIT_EVERY` rows within each type (and once more at the end
    of each type) so a long run doesn't hold one giant uncommitted
    transaction.

    Args:
        session: an active SQLAlchemy session.
        embedding_model: forwarded to `refresh_entity` for every entity.
        candidate_limit: caps how many candidates (ordered by id) are
            processed; `None` processes all of them.
        job_limit: same, for jobs.

    Returns:
        dict[str, int]: `{"computed": n, "skipped": n, "no_data": n}`
            summed across every entity processed, both types combined.
    """
    totals = {"computed": 0, "skipped": 0, "no_data": 0}
    limits = {"candidate": candidate_limit, "job": job_limit}

    for entity_type, id_query in ENTITY_ID_QUERIES.items():
        limit = limits.get(entity_type)
        stmt = id_query if limit is None else id_query.limit(limit)
        entity_ids = session.execute(stmt).scalars().all()

        for i, entity_id in enumerate(entity_ids):
            _merge(totals, refresh_entity(session, entity_type, entity_id, embedding_model))
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
