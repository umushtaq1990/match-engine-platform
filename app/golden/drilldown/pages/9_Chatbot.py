"""Chatbot: a RAG assistant over the knowledge base (job postings,
candidate profiles, skills, courses), via app.chatbot's decoupled package
(models/ingestion/retrieval/graph/eval -- none of it imports anything from
this drilldown app, so it's liftable into another project as-is). The one
page in this app that requires login (see auth.py's docstring for why).

Retrieval mode is vector-only this phase (see
docs/architecture/application-services/rag-chatbot.md's roadmap) -- the
"Sources" expander below shows exactly which chunks backed each answer,
the traceability this feature is explicitly meant to demonstrate.
"""
import streamlit as st

from app.chatbot.chat_history import create_session
from app.chatbot.graph import run_chat_turn
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


for turn in st.session_state["chatbot_display_history"]:
    with st.chat_message(turn["role"]):
        st.write(turn["content"])
        _render_sources(turn.get("sources", []))

question = st.chat_input("Ask about candidates, jobs, skills, or courses...")
if question:
    st.session_state["chatbot_display_history"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    session = SessionLocal()
    try:
        with st.spinner("Thinking..."):
            result = run_chat_turn(session, session_id, role, question)
    finally:
        session.close()

    with st.chat_message("assistant"):
        st.write(result["answer"])
        _render_sources(result["retrieved_chunks"])
    st.session_state["chatbot_display_history"].append({
        "role": "assistant", "content": result["answer"], "sources": result["retrieved_chunks"],
    })
