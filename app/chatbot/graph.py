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
from app.chatbot.llm_backend import GenerateFn
from app.chatbot.llm_backend import resolve_generate_fn as _resolve_generate_fn
from app.chatbot.tools import (
    TOOL_LOOKUP_STRUCTURED_DATA,
    TOOL_SEARCH_KNOWLEDGE_BASE,
    TOOL_SPECS,
    lookup_structured_data,
    search_knowledge_base,
)

# Ignored outright when `generate_fn` is vLLM's client (it serves one fixed
# model regardless of this string -- see app.chatbot.llm_backend).
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

lookup_structured_data's arguments MUST use exactly the key "entity_type", plus either "entity_id" \
(an integer) when the question names a candidate or job by id, e.g. {{"name": "lookup_structured_data", \
"arguments": {{"entity_type": "candidate", "entity_id": 1}}}} -- or "entity_name" (a string, the exact \
name/title) when the question names a skill or course, e.g. {{"name": "lookup_structured_data", \
"arguments": {{"entity_type": "skill", "entity_name": "Java"}}}}. Never use "candidate_id", "job_id", \
"skill_name", or "course_name"/"course_title" as a key name -- always "entity_type" plus "entity_id" or \
"entity_name".

MANDATORY rule, not a suggestion: "entity_name" is ONLY for when the entity itself is a skill or a \
course -- i.e. entity_type is "skill" or "course". Examples:
- "What category is the skill Java in?" -> {{"name": "lookup_structured_data", "arguments": \
{{"entity_type": "skill", "entity_name": "Java"}}}}
- "Who provides the course 'Intro to ML' (course id 7)?" -> {{"name": "lookup_structured_data", \
"arguments": {{"entity_type": "course", "entity_name": "Intro to ML"}}}}

MANDATORY rule, not a suggestion: for entity_type "candidate" or "job", ALWAYS use "entity_id" (the \
integer id), NEVER "entity_name" -- even when the question also mentions skills. The entity being \
looked up is the candidate/job itself (identified by its id); skills are just a field of it, not a \
separate lookup. Examples:
- "What skills are required for job 2522?" -> {{"name": "lookup_structured_data", "arguments": \
{{"entity_type": "job", "entity_id": 2522}}}} -- NOT entity_name: "2522".
- "What skills does candidate 7 have?" -> {{"name": "lookup_structured_data", "arguments": \
{{"entity_type": "candidate", "entity_id": 7}}}} -- NOT entity_name: "7".

Any question naming a specific candidate/job id, or a specific skill/course name, MUST use \
lookup_structured_data, never search_knowledge_base. Only use search_knowledge_base for open-ended/\
descriptive questions that name no specific candidate id, job id, skill name, or course title.

Conversation so far:
{history}

User's new question: {question}

Decide which tool(s) to call to answer this question, and with what arguments. Output a JSON array \
of {{"name": ..., "arguments": {{...}}}} objects. Call at most 2 tools.
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


def _decide_tools(generate_fn: GenerateFn, state: ChatTurnState) -> ChatTurnState:
    tool_specs_text = "\n".join(f"- {t['name']}: {t['description']}" for t in TOOL_SPECS)
    prompt = _DECIDE_TOOLS_PROMPT.format(
        tool_specs=tool_specs_text, history=state["history_text"] or "(none yet)", question=state["question"],
    )
    raw = generate_fn(_LLM_MODEL, prompt, response_format=_TOOL_CALL_SCHEMA)
    try:
        calls = json.loads(raw)
    except json.JSONDecodeError:
        calls = []
    if not isinstance(calls, list):
        calls = []
    state["tool_calls"] = calls[:2]
    return state


def _normalize_lookup_args(args: dict) -> dict:
    """`_TOOL_CALL_SCHEMA`'s `arguments` field is an unconstrained JSON
    object (Ollama's/vLLM's `format`/`response_format` schema only
    enforces the outer `{name, arguments}` shape, not per-tool argument
    key names) -- confirmed live: a real call came back as
    `{"candidate_id": "1"}` instead of the requested `{"entity_type":
    "candidate", "entity_id": 1}`. Rather than trusting a small model's
    exact key names, accept the common aliases directly.

    Returns:
        dict: `{"entity_type": str, "entity_id": int | None, "entity_name":
            str | None}`. `entity_type` is `""` when nothing recognized --
            a safe no-match sentinel (fails that one lookup, not the
            whole turn), not a parsing error.
    """
    if "candidate_id" in args:
        return {"entity_type": "candidate", "entity_id": int(args["candidate_id"]), "entity_name": None}
    if "job_id" in args:
        return {"entity_type": "job", "entity_id": int(args["job_id"]), "entity_name": None}
    if "skill_name" in args:
        return {"entity_type": "skill", "entity_id": None, "entity_name": str(args["skill_name"])}
    if "course_name" in args:
        return {"entity_type": "course", "entity_id": None, "entity_name": str(args["course_name"])}
    if "course_title" in args:
        return {"entity_type": "course", "entity_id": None, "entity_name": str(args["course_title"])}

    entity_type = str(args["entity_type"]) if "entity_type" in args else ""
    entity_id = int(args["entity_id"]) if "entity_id" in args else None
    entity_name = str(args["entity_name"]) if "entity_name" in args else None

    # candidate/job have no name-based lookup -- a small model sometimes
    # emits entity_name for them anyway (confirmed live: "what skills does
    # job 2522 require" -> {"entity_type": "job", "entity_name": "2522"}),
    # usually a numeric id mislabeled as a name. Recover it rather than
    # failing the lookup outright.
    if entity_type in ("candidate", "job") and entity_id is None and entity_name is not None:
        try:
            entity_id = int(entity_name)
            entity_name = None
        except ValueError:
            pass

    if not entity_type or (entity_id is None and entity_name is None):
        return {"entity_type": "", "entity_id": None, "entity_name": None}
    return {"entity_type": entity_type, "entity_id": entity_id, "entity_name": entity_name}


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
                normalized = _normalize_lookup_args(args)
                data = lookup_structured_data(
                    session, state["role"], normalized["entity_type"],
                    entity_id=normalized["entity_id"], entity_name=normalized["entity_name"],
                )
                results.append({"tool": name, "result": data})
        except Exception as exc:
            results.append({"tool": name, "result": f"(tool call failed: {exc})"})
    state["tool_results"] = results
    state["retrieved_chunks"] = retrieved_chunks
    return state


def _generate_answer(generate_fn: GenerateFn, state: ChatTurnState) -> ChatTurnState:
    if state["tool_results"]:
        context = json.dumps(state["tool_results"], indent=2, default=str)
    else:
        context = "(no information found)"
    prompt = _ANSWER_PROMPT.format(
        context=context, history=state["history_text"] or "(none yet)", question=state["question"],
    )
    state["answer"] = generate_fn(_LLM_MODEL, prompt).strip()
    return state


def _build_graph(session: Session, generate_fn: GenerateFn):
    workflow = StateGraph(ChatTurnState)
    workflow.add_node("decide_tools", lambda state: _decide_tools(generate_fn, state))
    workflow.add_node("execute_tools", lambda state: _execute_tools(session, state))
    workflow.add_node("generate_answer", lambda state: _generate_answer(generate_fn, state))
    workflow.set_entry_point("decide_tools")
    workflow.add_edge("decide_tools", "execute_tools")
    workflow.add_edge("execute_tools", "generate_answer")
    workflow.add_edge("generate_answer", END)
    return workflow.compile()


def _format_history(messages) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


@mlflow.trace(span_type=SpanType.CHAIN)
def run_chat_turn(
    session: Session, session_id: int, role: str, question: str, generate_fn: GenerateFn | None = None,
) -> dict:
    """Runs one full chat turn: persists the user's question, decides and
    executes tool calls, generates a grounded answer, persists it.

    Args:
        session: an active SQLAlchemy session.
        session_id: the `ChatSession` id (from `app.chatbot.chat_history.
            create_session`).
        role: the user's role, gating what the tools can retrieve.
        question: the user's new message.
        generate_fn: which LLM client's `generate()` to call -- defaults to
            Ollama (the live chat page's choice); the eval harness can pass
            vLLM's instead for real concurrent throughput (see
            `app.chatbot.llm_backend`).

    Returns:
        dict: `{"answer": str, "retrieved_chunks": list[dict] (each
            `{chunk_id, text, source_type, source_entity_id, tags,
            is_fresh}`, the traceability record), "tool_calls": list[dict]}`.
    """
    if generate_fn is None:
        generate_fn = _resolve_generate_fn("ollama")
    chat_history.append_message(session, session_id, "user", question)
    history_text = _format_history(chat_history.load_recent_messages(session, session_id, limit=10))

    graph = _build_graph(session, generate_fn)
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
        "tool_results": final_state["tool_results"],
    }


@mlflow.trace(span_type=SpanType.CHAIN)
def run_retrieval_turn(session: Session, role: str, question: str, generate_fn: GenerateFn | None = None) -> dict:
    """Runs only `decide_tools` -> `execute_tools` -- no answer generation,
    no `ChatSession`/`ChatMessage` persistence. Isolates retrieval
    correctness (which chunks/lookup the agent used) from answer-quality
    judging: precision@k/recall@k only need `tool_calls`/`retrieved_chunks`,
    and computing them doesn't require ever generating or judging an
    answer, which is ~4x the LLM calls per question for no change to
    these two numbers. Meant for fast iteration on retrieval knobs
    (embedding model, top_k, chunking) -- use `run_chat_turn` for a full
    scored run that also needs the LLM-judge metrics.

    Returns:
        dict: `{"retrieved_chunks": list[dict], "tool_calls": list[dict],
            "tool_results": list[dict]}` -- same shapes as `run_chat_turn`'s,
            minus `"answer"`.
    """
    if generate_fn is None:
        generate_fn = _resolve_generate_fn("ollama")
    state: ChatTurnState = {
        "question": question, "history_text": "", "role": role,
        "tool_calls": [], "tool_results": [], "retrieved_chunks": [], "answer": "",
    }
    state = _decide_tools(generate_fn, state)
    state = _execute_tools(session, state)
    return {
        "retrieved_chunks": state["retrieved_chunks"],
        "tool_calls": state["tool_calls"],
        "tool_results": state["tool_results"],
    }
