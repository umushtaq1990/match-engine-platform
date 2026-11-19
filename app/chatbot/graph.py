"""LangGraph orchestration for one chat turn -- the "connecting two tools"
part of the brief. qwen2.5:1.5b via Ollama has no native function-calling
API the way OpenAI does, so tool selection is done the same way this
project already gets structured output from it (`app.extraction.pipeline`'s
`response_format` JSON-schema trick): ask the LLM which tool(s) to call and
with what arguments, as JSON, then execute them in Python.

Graph shape: decide_tools -> execute_tools -> generate_answer -> persist.
Linear today, deliberately built as a graph (not a plain function chain) so
Phase 2/3 can add a real routing decision (vector vs. graph vs. hybrid
retrieval) without a rewrite -- see the plan this shipped from.
"""
import json
from typing import TypedDict

import mlflow
from langgraph.graph import END, StateGraph
from mlflow.entities import SpanType
from sqlalchemy.orm import Session

from app.chatbot import chat_history
from app.chatbot.tools import (
    TOOL_LOOKUP_STRUCTURED_DATA,
    TOOL_SEARCH_KNOWLEDGE_BASE,
    TOOL_SPECS,
    lookup_structured_data,
    search_knowledge_base,
)
from app.extraction.ollama_client import generate

_LLM_MODEL = "qwen2.5:1.5b"

_TOOL_CALL_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": [TOOL_SEARCH_KNOWLEDGE_BASE, TOOL_LOOKUP_STRUCTURED_DATA]},
            "arguments": {"type": "object"},
        },
        "required": ["name", "arguments"],
    },
}

_DECIDE_TOOLS_PROMPT = """You are an assistant with access to these tools:
{tool_specs}

lookup_structured_data's arguments MUST use exactly the keys "entity_type" and "entity_id", e.g. \
{{"name": "lookup_structured_data", "arguments": {{"entity_type": "candidate", "entity_id": 1}}}} \
-- never "candidate_id" or "job_id" as a key name.

Conversation so far:
{history}

User's new question: {question}

Decide which tool(s) to call to answer this question, and with what arguments. Output a JSON array \
of {{"name": ..., "arguments": {{...}}}} objects. Call at most 2 tools. If the question names a \
specific candidate or job id, prefer lookup_structured_data; otherwise use search_knowledge_base.
"""

_ANSWER_PROMPT = """Answer the user's question using ONLY the information below. Do not invent any \
detail not present here. Describe things neutrally -- an absent field is simply unknown, not a \
deficiency. If the information below doesn't answer the question, say so plainly rather than \
guessing.

Information:
{context}

Conversation so far:
{history}

User's question: {question}

Answer in 2-4 plain sentences.
"""


class ChatTurnState(TypedDict):
    question: str
    history_text: str
    role: str
    tool_calls: list[dict]
    tool_results: list[dict]
    retrieved_chunks: list[dict]
    answer: str


def _decide_tools(state: ChatTurnState) -> ChatTurnState:
    tool_specs_text = "\n".join(f"- {t['name']}: {t['description']}" for t in TOOL_SPECS)
    prompt = _DECIDE_TOOLS_PROMPT.format(
        tool_specs=tool_specs_text, history=state["history_text"] or "(none yet)", question=state["question"],
    )
    raw = generate(_LLM_MODEL, prompt, response_format=_TOOL_CALL_SCHEMA)
    try:
        calls = json.loads(raw)
    except json.JSONDecodeError:
        calls = []
    if not isinstance(calls, list):
        calls = []
    state["tool_calls"] = calls[:2]
    return state


def _normalize_lookup_args(args: dict) -> tuple[str, int]:
    """`_TOOL_CALL_SCHEMA`'s `arguments` field is an unconstrained JSON
    object (Ollama's `format` schema only enforces the outer `{name,
    arguments}` shape, not per-tool argument key names) -- confirmed live:
    a real call came back as `{"candidate_id": "1"}` instead of the
    requested `{"entity_type": "candidate", "entity_id": 1}`. Rather than
    trusting a small model's exact key names, accept the common aliases
    directly.
    """
    if "entity_type" in args and "entity_id" in args:
        return str(args["entity_type"]), int(args["entity_id"])
    if "candidate_id" in args:
        return "candidate", int(args["candidate_id"])
    if "job_id" in args:
        return "job", int(args["job_id"])
    return "", -1


def _execute_tools(session: Session, state: ChatTurnState) -> ChatTurnState:
    results = []
    retrieved_chunks: list[dict] = []
    for call in state["tool_calls"]:
        name = call.get("name")
        args = call.get("arguments") or {}
        try:
            if name == TOOL_SEARCH_KNOWLEDGE_BASE:
                chunks = search_knowledge_base(session, state["role"], args.get("query", state["question"]))
                results.append({"tool": name, "result": [c.text for c in chunks]})
                retrieved_chunks.extend({
                    "chunk_id": c.chunk_id, "text": c.text, "source_type": c.source_type,
                    "source_entity_id": c.source_entity_id, "tags": c.tags, "is_fresh": c.is_fresh,
                } for c in chunks)
            elif name == TOOL_LOOKUP_STRUCTURED_DATA:
                entity_type, entity_id = _normalize_lookup_args(args)
                data = lookup_structured_data(session, state["role"], entity_type, entity_id)
                results.append({"tool": name, "result": data})
        except Exception as exc:
            results.append({"tool": name, "result": f"(tool call failed: {exc})"})
    state["tool_results"] = results
    state["retrieved_chunks"] = retrieved_chunks
    return state


def _generate_answer(state: ChatTurnState) -> ChatTurnState:
    if state["tool_results"]:
        context = json.dumps(state["tool_results"], indent=2, default=str)
    else:
        context = "(no information found)"
    prompt = _ANSWER_PROMPT.format(
        context=context, history=state["history_text"] or "(none yet)", question=state["question"],
    )
    state["answer"] = generate(_LLM_MODEL, prompt).strip()
    return state


def _build_graph(session: Session):
    workflow = StateGraph(ChatTurnState)
    workflow.add_node("decide_tools", _decide_tools)
    workflow.add_node("execute_tools", lambda state: _execute_tools(session, state))
    workflow.add_node("generate_answer", _generate_answer)
    workflow.set_entry_point("decide_tools")
    workflow.add_edge("decide_tools", "execute_tools")
    workflow.add_edge("execute_tools", "generate_answer")
    workflow.add_edge("generate_answer", END)
    return workflow.compile()


def _format_history(messages) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


@mlflow.trace(span_type=SpanType.CHAIN)
def run_chat_turn(session: Session, session_id: int, role: str, question: str) -> dict:
    """Runs one full chat turn: persists the user's question, decides and
    executes tool calls, generates a grounded answer, persists it.

    Args:
        session: an active SQLAlchemy session.
        session_id: the `ChatSession` id (from `app.chatbot.chat_history.
            create_session`).
        role: the user's role, gating what the tools can retrieve.
        question: the user's new message.

    Returns:
        dict: `{"answer": str, "retrieved_chunks": list[dict] (each
            `{chunk_id, text, source_type, source_entity_id, tags,
            is_fresh}`, the traceability record), "tool_calls": list[dict]}`.
    """
    chat_history.append_message(session, session_id, "user", question)
    history_text = _format_history(chat_history.load_recent_messages(session, session_id, limit=10))

    graph = _build_graph(session)
    initial_state: ChatTurnState = {
        "question": question, "history_text": history_text, "role": role,
        "tool_calls": [], "tool_results": [], "retrieved_chunks": [], "answer": "",
    }
    final_state = graph.invoke(initial_state)

    chunk_ids = [c["chunk_id"] for c in final_state["retrieved_chunks"]]
    chat_history.append_message(session, session_id, "assistant", final_state["answer"], chunk_ids)
    return {
        "answer": final_state["answer"],
        "retrieved_chunks": final_state["retrieved_chunks"],
        "tool_calls": final_state["tool_calls"],
    }
