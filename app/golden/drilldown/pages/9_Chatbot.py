"""Chatbot: a RAG assistant over the knowledge base (job postings,
candidate profiles, skills, courses), via app.chatbot's decoupled package
(models/ingestion/retrieval/graph/eval -- none of it imports anything from
this drilldown app, so it's liftable into another project as-is). The one
page in this app that requires login (see auth.py's docstring for why).

Vector search, exact-id/name lookup, knowledge-graph relationship queries
(Phase 2), and whole-pool criteria ranking (the "Candidate/job criteria
search & ranking" Roadmap item) are all available to the agent -- see
docs/architecture/application-services/rag-chatbot.md's roadmap. The
"Sources" expander below shows exactly which chunks backed each answer,
and the thumbs up/down under each reply feeds per-message quality
feedback back to app.chatbot.chat_history.save_feedback -- both part of
the traceability this feature is explicitly meant to demonstrate.
"""
import streamlit as st

from app.chatbot.chat_history import create_session, save_feedback
from app.chatbot.graph import run_chat_turn
from app.chatbot.models import FEEDBACK_DOWN, FEEDBACK_UP
from app.db import SessionLocal
from app.golden.drilldown.auth import require_login

st.set_page_config(page_title="Chatbot", layout="wide")

user_id, role = require_login()
st.title("Knowledge base chatbot")
st.caption(f"Signed in with role: **{role}**")

if "chatbot_session_id" not in st.session_state:
    session = SessionLocal()
    try:
        chat_session = create_session(session, user_id, role)
    finally:
        session.close()
    st.session_state["chatbot_session_id"] = chat_session.id
    st.session_state["chatbot_display_history"] = []

session_id = st.session_state["chatbot_session_id"]


def _render_sources(sources: list[dict]) -> None:
    if not sources:
        return
    with st.expander("Sources"):
        for source in sources:
            staleness = "" if source.get("is_fresh", True) else " — _stale, re-ingestion pending_"
            st.markdown(
                f"- **{source['source_type']}** #{source['source_entity_id']} "
                f"(tags: {', '.join(source['tags']) or 'none'}){staleness}"
            )
            st.caption(source["text"][:300])


def _render_feedback(message_id: int) -> None:
    """Thumbs up/down on one assistant message, saved immediately on
    click (standard chat-product pattern -- ChatGPT/Claude/Copilot all
    save the rating instantly, not behind a separate confirm step). A
    comment box appears only after thumbs-down, since that's the case
    that actually needs explaining -- forcing a comment on every rating
    would suppress the easy positive signal. See
    app.chatbot.chat_history.save_feedback's docstring for why this is
    per-message (traceable to the exact answer), not an end-of-session
    survey.
    """
    rating_key = f"chatbot_rating_{message_id}"
    current = st.session_state.get(rating_key)
    up_col, down_col, _ = st.columns([1, 1, 10])
    if up_col.button("👍", key=f"chatbot_up_{message_id}"):
        db_session = SessionLocal()
        try:
            save_feedback(db_session, message_id, FEEDBACK_UP)
        finally:
            db_session.close()
        st.session_state[rating_key] = FEEDBACK_UP
        st.rerun()
    if down_col.button("👎", key=f"chatbot_down_{message_id}"):
        db_session = SessionLocal()
        try:
            save_feedback(db_session, message_id, FEEDBACK_DOWN)
        finally:
            db_session.close()
        st.session_state[rating_key] = FEEDBACK_DOWN
        st.rerun()

    if current == FEEDBACK_UP:
        st.caption("Thanks for the feedback! 👍")
    elif current == FEEDBACK_DOWN:
        st.caption("Thanks for the feedback — sorry this missed the mark.")
        comment = st.text_input("What went wrong? (optional)", key=f"chatbot_comment_{message_id}")
        if st.button("Submit comment", key=f"chatbot_submit_comment_{message_id}") and comment:
            db_session = SessionLocal()
            try:
                save_feedback(db_session, message_id, FEEDBACK_DOWN, comment)
            finally:
                db_session.close()
            st.success("Comment saved, thank you.")


for turn in st.session_state["chatbot_display_history"]:
    with st.chat_message(turn["role"]):
        st.write(turn["content"])
        _render_sources(turn.get("sources", []))
        if turn["role"] == "assistant" and turn.get("message_id") is not None:
            _render_feedback(turn["message_id"])

question = st.chat_input("Ask about candidates, jobs, skills, or courses...")
if question:
    st.session_state["chatbot_display_history"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    session = SessionLocal()
    try:
        with st.spinner("Thinking..."):
            result = run_chat_turn(
                session, session_id, role, question, enable_graph_tool=True, enable_rank_tool=True,
            )
    finally:
        session.close()

    with st.chat_message("assistant"):
        st.write(result["answer"])
        _render_sources(result["retrieved_chunks"])
        _render_feedback(result["message_id"])
    st.session_state["chatbot_display_history"].append({
        "role": "assistant", "content": result["answer"], "sources": result["retrieved_chunks"],
        "message_id": result["message_id"],
    })
