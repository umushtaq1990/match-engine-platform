"""Persisted chat history/state -- Postgres-backed, not `st.session_state`
(what both the Sanabil_POC reference and this project's existing frontend
use, neither durable past a browser session). Thin CRUD only; the LangGraph
graph (app/chatbot/graph.py) is what decides *when* to call these.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chatbot.models import ChatMessage, ChatSession


def create_session(session: Session, user_id: str, role: str) -> ChatSession:
    """Starts a new chat session, committing it immediately so its `id`
    is usable right away by the caller (e.g. to store in `st.session_state`
    for the rest of the browser session).
    """
    chat_session = ChatSession(user_id=user_id, role=role, created_at=datetime.now(timezone.utc))
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session


def append_message(
    session: Session, session_id: int, role: str, content: str, retrieved_chunk_ids: list | None = None,
) -> ChatMessage:
    """Appends one turn. `retrieved_chunk_ids` is the traceability record
    for an assistant message (empty list for a user message).
    """
    message = ChatMessage(
        session_id=session_id, role=role, content=content,
        retrieved_chunk_ids=retrieved_chunk_ids or [], created_at=datetime.now(timezone.utc),
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def load_recent_messages(session: Session, session_id: int, limit: int = 20) -> list[ChatMessage]:
    """Returns up to `limit` most recent messages for a session, oldest
    first (ready to feed straight into a chat prompt's history, no
    reversal needed by the caller).
    """
    rows = session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return list(reversed(rows))
