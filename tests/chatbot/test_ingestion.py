"""Regression test for a real bug caught live: a full-population embedding
run crashed with a 500 from Ollama's /api/embeddings because a job
description chunk exceeded all-minilm's hard 256-token context limit --
`_build_job_chunks` used the full, untruncated text as one chunk.
`_split_into_chunks` is the fix; this test locks the split behavior in.
"""
from app.chatbot.ingestion import _MAX_CHUNK_CHARS, _split_into_chunks


def test_short_text_is_not_split():
    assert _split_into_chunks("short text") == ["short text"]


def test_long_text_is_split_into_bounded_pieces():
    long_text = "x" * (_MAX_CHUNK_CHARS * 3 + 50)
    pieces = _split_into_chunks(long_text)
    assert len(pieces) == 4
    assert all(len(piece) <= _MAX_CHUNK_CHARS for piece in pieces)


def test_split_pieces_reconstruct_the_original_text():
    long_text = "abcdefghij" * 200
    pieces = _split_into_chunks(long_text)
    assert "".join(pieces) == long_text
