"""Vector retrieval over `DocumentChunk`/`EntityEmbedding` -- Phase 1's only
retrieval mode (see the plan this shipped from: Phase 2 adds a graph-based
tool, Phase 3 a hybrid mode + reranking, both as additions here, not a
rewrite). Same two-stage "ANN query, not a brute-force scan" shape as
`app.embeddings.retrieval`'s `_stage1_job_ids`.
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chatbot.access_control import allowed_visibilities
from app.chatbot.chunk_embeddings import DIMENSION, ENTITY_TYPE
from app.chatbot.models import DEFAULT_CHUNK_EMBEDDING_MODEL, DocumentChunk
from app.embeddings.models import EntityEmbedding
from app.extraction.ollama_client import embed


@dataclass
class RetrievedChunk:
    """Attributes:
    chunk_id: the `DocumentChunk`'s primary key.
    text: the chunk's full text.
    source_type: traceability -- which kind of domain row this came from.
    source_entity_id: traceability -- which specific row.
    tags: the chunk's tags, for display in the "Sources" panel.
    is_fresh: `False` if this chunk's embedding is stale relative to its
        current `content_hash` (the embedding refresh job hasn't caught
        up with an edit yet) -- surfaced, not hidden, so a caller can
        choose to warn rather than silently serve outdated content.
    """

    chunk_id: int
    text: str
    source_type: str
    source_entity_id: int
    tags: list
    is_fresh: bool


def retrieve_chunks(
    session: Session, query_text: str, role: str, top_k: int = 5,
    tags_filter: list[str] | None = None, embedding_model: str = DEFAULT_CHUNK_EMBEDDING_MODEL,
) -> list[RetrievedChunk]:
    """Embeds `query_text` and finds the `top_k` nearest `DocumentChunk`s by
    cosine distance, restricted to what `role` is allowed to see.

    Args:
        session: an active SQLAlchemy session.
        query_text: the user's question (or any text to find similar
            chunks for).
        role: gates `DocumentChunk.visibility` via
            `app.chatbot.access_control.allowed_visibilities` -- the
            actual access-control enforcement point, not the UI layer.
        top_k: how many chunks to return.
        tags_filter: if given, only chunks whose `tags` intersects this
            list are eligible (e.g. `["job"]` to search postings only).
        embedding_model: which model's stored chunk vectors to search.

    Returns:
        list[RetrievedChunk]: nearest first, each flagged `is_fresh`.
    """
    query_vector = embed(embedding_model, query_text)
    allowed = allowed_visibilities(role)

    stmt = (
        select(EntityEmbedding, DocumentChunk)
        .join(DocumentChunk, DocumentChunk.id == EntityEmbedding.entity_id)
        .where(
            EntityEmbedding.entity_type == ENTITY_TYPE,
            EntityEmbedding.dimension == DIMENSION,
            EntityEmbedding.embedding_model == embedding_model,
            DocumentChunk.visibility.in_(allowed),
        )
        .order_by(EntityEmbedding.vector.cosine_distance(query_vector))
        .limit(top_k * 3 if tags_filter else top_k)
    )
    rows = session.execute(stmt).all()

    results = []
    for embedding_row, chunk in rows:
        if tags_filter and not set(chunk.tags or []).intersection(tags_filter):
            continue
        results.append(RetrievedChunk(
            chunk_id=chunk.id, text=chunk.text, source_type=chunk.source_type,
            source_entity_id=chunk.source_entity_id, tags=chunk.tags or [],
            is_fresh=embedding_row.source_hash == chunk.content_hash,
        ))
        if len(results) >= top_k:
            break
    return results
