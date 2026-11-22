"""Computes/refreshes embeddings for `DocumentChunk` rows, stored in
`app.embeddings.models.EntityEmbedding` with `entity_type="document_chunk"`,
`dimension="chunk"` -- reusing that table's own documented generic design
(see its docstring) rather than a parallel vector column.

Deliberately NOT registered into `app.embeddings.compute_embeddings`'s
`ENTITY_ID_QUERIES`/`DIMENSION_HASHERS` dicts: that loop assumes every
registered entity type shares the same three dimensions (skills/experience/
overall) and calls each dimension's hasher/text-builder for whatever entity
type it's given. A document chunk has exactly one dimension of its own
("chunk", not three) and no skills/experience/overall concept at all --
plugging it into those dicts would make `refresh_entity` call e.g.
`skills_source_hash(session, "document_chunk", chunk_id)`, which raises
`ValueError` (no candidate/job-shaped skills exist for a chunk). This
module is the correct, independent extension instead: same hash-then-skip
discipline, EntityEmbedding reused as-is, but its own small refresh loop.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chatbot.models import DEFAULT_CHUNK_EMBEDDING_MODEL, DocumentChunk
from app.embeddings.models import EntityEmbedding
from app.extraction.ollama_client import embed

ENTITY_TYPE = "document_chunk"
DIMENSION = "chunk"
COMMIT_EVERY = 50


def refresh_chunk_embedding(
    session: Session, chunk: DocumentChunk, embedding_model: str = DEFAULT_CHUNK_EMBEDDING_MODEL,
) -> bool:
    """Recomputes one chunk's embedding if stale/missing.

    A chunk's own `content_hash` (set by `app.chatbot.ingestion`, already a
    hash of its text) doubles as this embedding's source hash -- no need to
    recompute a second hash of the same text.

    Args:
        session: an active SQLAlchemy session; caller commits.
        chunk: the `DocumentChunk` to (re)embed.
        embedding_model: which model to use.

    Returns:
        bool: `True` if a real `embed()` call was made (new or changed
            text), `False` if the stored embedding was already current.
    """
    existing = session.execute(
        select(EntityEmbedding).where(
            EntityEmbedding.entity_type == ENTITY_TYPE,
            EntityEmbedding.entity_id == chunk.id,
            EntityEmbedding.dimension == DIMENSION,
            EntityEmbedding.embedding_model == embedding_model,
        )
    ).scalar_one_or_none()

    if existing is not None and existing.source_hash == chunk.content_hash:
        return False

    vector = embed(embedding_model, chunk.text)
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.vector = vector
        existing.source_hash = chunk.content_hash
        existing.computed_at = now
    else:
        session.add(EntityEmbedding(
            entity_type=ENTITY_TYPE, entity_id=chunk.id, dimension=DIMENSION,
            embedding_model=embedding_model, source_hash=chunk.content_hash, vector=vector,
            computed_at=now,
        ))
    return True


def refresh_all_chunk_embeddings(
    session: Session, embedding_model: str = DEFAULT_CHUNK_EMBEDDING_MODEL,
) -> dict[str, int]:
    """Refreshes every `DocumentChunk`'s embedding, committing every
    `COMMIT_EVERY` rows (same batching discipline as
    `app.embeddings.compute_embeddings.refresh_all`).

    Returns:
        dict[str, int]: `{"computed": n, "skipped": n}`.
    """
    stats = {"computed": 0, "skipped": 0}
    chunk_ids = session.execute(select(DocumentChunk.id).order_by(DocumentChunk.id)).scalars().all()
    for i, chunk_id in enumerate(chunk_ids):
        chunk = session.get(DocumentChunk, chunk_id)
        if refresh_chunk_embedding(session, chunk, embedding_model):
            stats["computed"] += 1
        else:
            stats["skipped"] += 1
        if (i + 1) % COMMIT_EVERY == 0:
            session.commit()
    session.commit()
    return stats
