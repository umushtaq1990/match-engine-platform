"""Vector retrieval over `DocumentChunk`/`EntityEmbedding` -- Phase 1's only
retrieval mode (see the plan this shipped from: Phase 2 adds a graph-based
tool, Phase 3 a hybrid mode + reranking, both as additions here, not a
rewrite). Same two-stage "ANN query, not a brute-force scan" shape as
`app.embeddings.retrieval`'s `_stage1_job_ids`.
"""
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.chatbot.access_control import allowed_visibilities
from app.chatbot.chunk_embeddings import DIMENSION, ENTITY_TYPE
from app.chatbot.models import DEFAULT_CHUNK_EMBEDDING_MODEL, DocumentChunk
from app.embeddings.models import EntityEmbedding
from app.extraction.ollama_client import embed

# `entity_embeddings`'s single HNSW index (`ix_entity_embeddings_ann`) covers
# EVERY entity_type/dimension/embedding_model combination mixed together
# (candidate experience/skills/overall vectors, job experience/skills/overall
# vectors, AND these document_chunk vectors all in one index) -- document
# chunks are only ~40% of the table. pgvector's documented behavior for
# `ORDER BY ... <=> ... LIMIT k` combined with a WHERE filter on other
# columns is to fetch only `hnsw.ef_search` (default 40) globally-nearest
# candidates from the index, THEN apply the filter -- so if a query's
# nearest neighbors happen to skew toward non-chunk rows, the filtered
# result can come back short, even empty, regardless of how many genuinely
# relevant chunks exist. Confirmed live, not assumed: "mechanical
# engineering" returned 0 of 5 requested chunks at the default ef_search,
# and a second query ("what training is available for project management")
# returned 4 of 5 -- both real, previously-unexplained retrieval misses.
# Raising ef_search widens that pre-filter candidate pool. Measured directly
# (5 real queries): ef_search=40 missed on 2/5; _HNSW_EF_SEARCH found the
# full requested count on all 5, adding single-digit-to-low-hundreds of ms
# (worst case ~90ms) -- negligible next to this page's 8-25s LLM round
# trip. ef_search=1000 was also tried and discarded: no better recall than
# 400, but ~1.5s slower per call.
_HNSW_EF_SEARCH = 400


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

    # See _HNSW_EF_SEARCH's module-level comment -- without this, the
    # entity_type/dimension/embedding_model filter below can silently
    # starve the ANN search of real matches.
    session.execute(text(f"SET hnsw.ef_search = {_HNSW_EF_SEARCH}"))

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
