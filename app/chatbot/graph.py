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
from app.chatbot.guardrails import check_input_safety, redact_pii, redact_pii_in_value
from app.chatbot.llm_backend import GenerateFn
from app.chatbot.llm_backend import resolve_generate_fn as _resolve_generate_fn
from app.chatbot.tools import (
    GRAPH_TOOL_SPEC,
    TOOL_LOOKUP_GRAPH_CONTEXT,
    TOOL_LOOKUP_STRUCTURED_DATA,
    TOOL_SEARCH_KNOWLEDGE_BASE,
    TOOL_SPECS,
    lookup_graph_context,
    lookup_structured_data,
    search_knowledge_base,
)

# Ignored outright when `generate_fn` is vLLM's client (it serves one fixed
# model regardless of this string -- see app.chatbot.llm_backend). Bumped
# from qwen2.5:1.5b (2026-11-21): live user testing surfaced the small
# model repeatedly hallucinating a tool call (a made-up entity_id, or a
# plausible-looking but wrong one) for questions no tool can actually
# answer -- "what are trending skills," "top 3 candidates with a data
# engineering profile" -- rather than following this file's own
# decide_tools prompt instruction to output [] and decline.
#
# Tried qwen2.5:7b first: confirmed live, same prompts, it reliably
# follows that instruction where 1.5b did not -- but also confirmed live
# it's too slow on this CPU-only host for interactive chat (the user
# tried it, got no response in a reasonable wait). qwen2.5:3b matched
# 7b's correct decline behavior on every case tested (same two questions
# above: [] tool calls, honest decline + scope reminder) while being
# dramatically faster (~8-25s vs. 7b's "no response") -- the practical
# sweet spot, not just a compromise. See
# docs/architecture/application-services/rag-chatbot.md.
_LLM_MODEL = "qwen2.5:3b"

# Matches run_chatbot_eval.py's/run_retrieval_eval.py's own constants of the
# same name -- kept in sync manually (no shared import) since those scripts
# also set this via mlflow.set_experiment() themselves, but only *after*
# their eval loop already finished calling run_chat_turn/run_retrieval_turn.
# Confirmed live: every trace from both the live chat page and every past
# eval run landed in MLflow's "Default" experiment (id 0), undiscoverable
# by name, because nothing set the active experiment before the traced call
# actually ran. Setting it here, at the top of the traced function itself,
# fixes both callers at once.
_MLFLOW_CHAT_EXPERIMENT = "jme-chatbot-rag"
_MLFLOW_RETRIEVAL_EXPERIMENT = "jme-chatbot-retrieval"


def _set_mlflow_experiment(experiment_name: str) -> None:
    """Best-effort, same discipline as ollama_client.py's
    `_set_span_attributes` -- observability must never break a real chat
    turn, so MLflow being unreachable/misconfigured is swallowed, not
    raised.

    MUST be called *before* entering the `@mlflow.trace`-decorated function
    whose trace it's meant to land correctly -- confirmed live:
    `mlflow.set_experiment()` resets the async trace-export processor, and
    calling it *inside* an already-started trace kills that same span
    ("Processor is already shutdown, ignoring call" -- the span silently
    never gets exported at all, not just mis-filed). That's why this isn't
    folded into `_tag_current_trace` below and called from inside
    `run_chat_turn`/`run_retrieval_turn` directly -- see their small
    `_run_chat_turn`/`_run_retrieval_turn` wrapper split instead."""
    try:
        mlflow.set_experiment(experiment_name)
    except Exception:
        pass


def _tag_current_trace(session_id: int | None, role: str, question: str) -> None:
    """Best-effort (see `_set_mlflow_experiment` above). Tags the *current*
    trace with `session_id`/`user` (MLflow's own convention -- stored as
    `mlflow.trace.session`/`mlflow.trace.user` metadata, filterable in the
    UI) plus a `question` tag, so a slow or failed trace can be traced back
    to the chat session and user role that caused it, not just "some call
    at some time took 60s". Safe to call from inside the traced function
    (unlike `_set_mlflow_experiment`) -- this only updates the already-
    active span, it doesn't reset the exporter."""
    try:
        kwargs = {"user": role, "tags": {"question": question[:200]}}
        if session_id is not None:
            kwargs["session_id"] = str(session_id)
        mlflow.update_current_trace(**kwargs)
    except Exception:
        pass


def _tool_call_schema(enable_graph_tool: bool) -> dict:
    """`lookup_graph_context` is only a legal choice when the caller opts
    in -- see `_build_graph`'s `enable_graph_tool` param and the Phase 2
    plan's "measure before adopting" decision; the live chat page's default
    (`enable_graph_tool=False`) must keep seeing exactly the old 2-tool
    enum, unchanged."""
    names = [TOOL_SEARCH_KNOWLEDGE_BASE, TOOL_LOOKUP_STRUCTURED_DATA]
    if enable_graph_tool:
        names.append(TOOL_LOOKUP_GRAPH_CONTEXT)
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": names},
                "arguments": {"type": "object"},
            },
            "required": ["name", "arguments"],
        },
    }

# Live user feedback (2026-11-21): a real admin session showed the small
# model handling "hi"/"how are you" with no framing of what the bot is for
# (odd third-person replies, no onboarding), and "what are trending skills"
# -- a question NO tool here can actually answer (no aggregate/trend tool
# exists, only exact-id lookup, vector search, and relationship lookup) --
# forced a garbage lookup_structured_data call (entity_id="trending_skills")
# instead of admitting it couldn't help. _BOT_SCOPE is injected into both
# _DECIDE_TOOLS_PROMPT (so the model can choose "no tool applies") and
# _ANSWER_PROMPT (so the model explains its scope instead of floundering on
# empty/failed tool results).
_BOT_SCOPE = (
    "You are the recruiting assistant for this platform's internal talent database. You can answer "
    "questions about: a specific candidate's profile/skills/experience (by id), a specific job's "
    "requirements (by id), what a specific skill or course covers (by name/title){graph_scope}. You "
    "CANNOT answer general-knowledge questions, small talk beyond a brief greeting, aggregate/trend "
    "questions across the whole dataset (e.g. \"what are the most popular/trending skills\", \"how many "
    "candidates are there\"), or anything unrelated to this platform's candidates/jobs/skills/courses."
)
_BOT_SCOPE_GRAPH_ADDITION = (
    ", and which candidates/jobs are similar to one you name by id, or courses relevant to a job"
)


def _bot_scope(enable_graph_tool: bool) -> str:
    return _BOT_SCOPE.format(graph_scope=_BOT_SCOPE_GRAPH_ADDITION if enable_graph_tool else "")


_DECIDE_TOOLS_PROMPT = """{scope}

You have access to these tools:
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
- "Who provides the course 'Intro to ML'?" (no id stated) -> {{"name": "lookup_structured_data", \
"arguments": {{"entity_type": "course", "entity_name": "Intro to ML"}}}}

MANDATORY rule, not a suggestion: if the question states a course's id explicitly (e.g. "course id \
7"), use "entity_id" for it instead of "entity_name" -- some course titles aren't unique, and the id \
resolves that unambiguously while the title alone might not. Example:
- "Who provides the course 'Intro to ML' (course id 7)?" -> {{"name": "lookup_structured_data", \
"arguments": {{"entity_type": "course", "entity_id": 7}}}} -- NOT entity_name: "Intro to ML".

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
{graph_tool_rule}
MANDATORY rule, not a suggestion: if the question is small talk/a greeting, a general-knowledge \
question, an aggregate/trend question (e.g. "what skills are trending", "how many candidates are \
there"), or anything else no tool above can actually answer, output an empty array [] -- do NOT force \
a tool call with a made-up or guessed argument just to call something.

Conversation so far:
{history}

User's new question: {question}

Decide which tool(s) to call to answer this question, and with what arguments. Output a JSON array \
of {{"name": ..., "arguments": {{...}}}} objects, or [] if no tool applies (see the rule above). Call \
at most 2 tools.
"""

# Only spliced into _DECIDE_TOOLS_PROMPT when enable_graph_tool=True --
# empty string otherwise, so the live chat page's default prompt is
# byte-for-byte unchanged.
_GRAPH_TOOL_RULE = """
MANDATORY rule, not a suggestion: a question asking who/what is SIMILAR TO, MATCHES, or RELATED TO \
one named candidate/job (not the entity's own facts) MUST use lookup_graph_context, with \
"entity_type" ("candidate" or "job") and "entity_id" (its integer id). Examples:
- "Which other candidates are similar to candidate 7?" -> {"name": "lookup_graph_context", \
"arguments": {"entity_type": "candidate", "entity_id": 7}}
- "What jobs best match candidate 7's skillset?" -> {"name": "lookup_graph_context", "arguments": \
{"entity_type": "candidate", "entity_id": 7}}
- "What courses would help someone targeting job 42?" -> {"name": "lookup_graph_context", \
"arguments": {"entity_type": "job", "entity_id": 42}}
Do NOT use lookup_graph_context for a question about the entity's own exact facts (its own skills, \
experience, title) -- that is still lookup_structured_data.
"""

_ANSWER_PROMPT = """{scope}

If the user's new question is just a greeting or small talk (not an actual question), reply briefly \
and warmly in 1 sentence, then in a second sentence tell them what you can help with. If the \
question is outside what you can help with (see above -- general knowledge, aggregate/trend stats, \
anything not about one specific candidate/job/skill/course in this system), say plainly in 1-2 \
sentences that you can't help with that specific thing, then remind them what you CAN help with -- \
do not guess or make up an answer for it.

Otherwise, answer the user's question using ONLY the information below. Do not invent any detail not \
present here. Describe things neutrally -- an absent field is simply unknown, not a deficiency. If \
the information below doesn't answer the question, say so plainly rather than guessing.

Information:
{context}

Conversation so far:
{history}

User's question: {question}

Answer in 2-4 plain sentences.
"""

# A tighter alternative to _ANSWER_PROMPT, for the relevance/coherence
# prompt experiment (app/chatbot/eval/run_chatbot_eval.py's
# --answer-prompt-variant) -- hypothesis: a small model given an open-ended
# "answer in 2-4 sentences" instruction sometimes hedges, repeats the
# question, or pads with filler, which a judge scores as less relevant/
# coherent than a direct, single-fact-first answer would be. Same grounding
# rules as the default, just more directive about *how* to state the fact.
_ANSWER_PROMPT_TIGHT = """{scope}

If the user's new question is a greeting/small talk or outside what you can help with (see above), \
reply in 1-2 sentences accordingly (briefly and warmly for a greeting, or plainly declining and \
restating your scope otherwise) -- do not guess an answer for it.

Otherwise, using ONLY the information below, answer the user's question in ONE direct sentence that \
states the fact itself first -- do not restate the question, do not hedge, do not add caveats unless \
the information is genuinely missing. Do not invent any detail not present here. If the information \
below doesn't answer the question, say so in one plain sentence rather than guessing.

Information:
{context}

Conversation so far:
{history}

User's question: {question}
"""


class ChatTurnState(TypedDict):
    question: str
    history_text: str
    role: str
    tool_calls: list[dict]
    tool_results: list[dict]
    retrieved_chunks: list[dict]
    answer: str


def _decide_tools(
    generate_fn: GenerateFn, model: str, enable_graph_tool: bool, state: ChatTurnState,
) -> ChatTurnState:
    specs = [*TOOL_SPECS, GRAPH_TOOL_SPEC] if enable_graph_tool else TOOL_SPECS
    tool_specs_text = "\n".join(f"- {t['name']}: {t['description']}" for t in specs)
    prompt = _DECIDE_TOOLS_PROMPT.format(
        scope=_bot_scope(enable_graph_tool), tool_specs=tool_specs_text,
        graph_tool_rule=_GRAPH_TOOL_RULE if enable_graph_tool else "",
        history=state["history_text"] or "(none yet)", question=state["question"],
    )
    raw = generate_fn(model, prompt, response_format=_tool_call_schema(enable_graph_tool))
    try:
        calls = json.loads(raw)
    except json.JSONDecodeError:
        calls = []
    if not isinstance(calls, list):
        calls = []
    state["tool_calls"] = calls[:2]
    return state


def _normalize_lookup_args(args: dict) -> dict:
    """`_tool_call_schema`'s `arguments` field is an unconstrained JSON
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
                # Output guardrail: redact before this text ever reaches the
                # answer-generation prompt or the "Sources" panel, not just
                # before the final answer -- see app.chatbot.guardrails'
                # module docstring (defense in depth against a future
                # ingestion pass embedding raw PII in chunk text).
                redacted_texts = [redact_pii(c.text)[0] for c in chunks]
                results.append({"tool": name, "result": redacted_texts})
                retrieved_chunks.extend({
                    "chunk_id": c.chunk_id, "text": redacted_text, "source_type": c.source_type,
                    "source_entity_id": c.source_entity_id, "tags": c.tags, "is_fresh": c.is_fresh,
                } for c, redacted_text in zip(chunks, redacted_texts))
            elif name == TOOL_LOOKUP_STRUCTURED_DATA:
                normalized = _normalize_lookup_args(args)
                data = lookup_structured_data(
                    session, state["role"], normalized["entity_type"],
                    entity_id=normalized["entity_id"], entity_name=normalized["entity_name"],
                )
                results.append({"tool": name, "result": redact_pii_in_value(data)[0]})
            elif name == TOOL_LOOKUP_GRAPH_CONTEXT:
                # Only ever emitted by decide_tools when enable_graph_tool
                # was True (see _tool_call_schema) -- unconditional here is
                # harmless, not a bypass of that gate.
                entity_type = str(args.get("entity_type") or "")
                entity_id = args.get("entity_id")
                entity_id = int(entity_id) if entity_id is not None else None
                data = lookup_graph_context(session, state["role"], entity_type, entity_id)
                results.append({"tool": name, "result": redact_pii_in_value(data)[0]})
        except Exception:
            # Deliberately not the raw exception text -- confirmed live:
            # leaking it (e.g. "invalid literal for int() with base 10:
            # 'trending_skills'") into generate_answer's context gave the
            # small model something to narrate/half-explain instead of
            # cleanly recognizing "no usable information," even with
            # _ANSWER_PROMPT's explicit out-of-scope instruction. A plain,
            # consistent phrase matches that instruction's framing instead.
            results.append({"tool": name, "result": "(no information available for this request)"})
    state["tool_results"] = results
    state["retrieved_chunks"] = retrieved_chunks
    return state


def _generate_answer(
    generate_fn: GenerateFn, model: str, answer_prompt: str, enable_graph_tool: bool, state: ChatTurnState,
) -> ChatTurnState:
    if state["tool_results"]:
        context = json.dumps(state["tool_results"], indent=2, default=str)
    else:
        context = "(no information found)"
    prompt = answer_prompt.format(
        scope=_bot_scope(enable_graph_tool), context=context,
        history=state["history_text"] or "(none yet)", question=state["question"],
    )
    # 2-4 sentences needs well under 100 completion tokens normally, but
    # vllm_client's old hardcoded default (250) was flagged as a plausible
    # cause of answers that looked cut off mid-thought -- explicit headroom
    # here rules that out rather than leaving it an open question.
    answer = generate_fn(model, prompt, max_tokens=400).strip()
    # Output guardrail, last line of defense: even though tool results are
    # already redacted above, this also catches anything the model itself
    # might invent in a PII shape (it has already been seen hallucinating
    # facts not present in any tool result -- see graph_retrieval.py's
    # subject_candidate_id fix history) and anything from a future tool
    # that doesn't go through _execute_tools' redaction.
    answer, redacted = redact_pii(answer)
    if redacted:
        try:
            mlflow.update_current_trace(tags={"guardrail_triggered": "output_pii_redacted"})
        except Exception:
            pass
    state["answer"] = answer
    return state


def _build_graph(
    session: Session, generate_fn: GenerateFn, model: str = _LLM_MODEL, answer_prompt: str = _ANSWER_PROMPT,
    enable_graph_tool: bool = False,
):
    workflow = StateGraph(ChatTurnState)
    workflow.add_node(
        "decide_tools", lambda state: _decide_tools(generate_fn, model, enable_graph_tool, state),
    )
    workflow.add_node("execute_tools", lambda state: _execute_tools(session, state))
    workflow.add_node(
        "generate_answer",
        lambda state: _generate_answer(generate_fn, model, answer_prompt, enable_graph_tool, state),
    )
    workflow.set_entry_point("decide_tools")
    workflow.add_edge("decide_tools", "execute_tools")
    workflow.add_edge("execute_tools", "generate_answer")
    workflow.add_edge("generate_answer", END)
    return workflow.compile()


def _format_history(messages) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


def run_chat_turn(
    session: Session, session_id: int, role: str, question: str, generate_fn: GenerateFn | None = None,
    answer_prompt: str = _ANSWER_PROMPT, model: str = _LLM_MODEL, enable_graph_tool: bool = False,
) -> dict:
    """Thin wrapper -- sets the active MLflow experiment *before* entering
    `_run_chat_turn`'s `@mlflow.trace`-decorated body (see
    `_set_mlflow_experiment`'s docstring for why this can't be done from
    inside that function instead), then delegates. See `_run_chat_turn` for
    the actual docstring/behavior; kept separate only for this ordering
    constraint, not a real API split."""
    _set_mlflow_experiment(_MLFLOW_CHAT_EXPERIMENT)
    return _run_chat_turn(
        session, session_id, role, question, generate_fn=generate_fn,
        answer_prompt=answer_prompt, model=model, enable_graph_tool=enable_graph_tool,
    )


@mlflow.trace(span_type=SpanType.CHAIN)
def _run_chat_turn(
    session: Session, session_id: int, role: str, question: str, generate_fn: GenerateFn | None = None,
    answer_prompt: str = _ANSWER_PROMPT, model: str = _LLM_MODEL, enable_graph_tool: bool = False,
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
        answer_prompt: the `generate_answer` node's prompt template --
            defaults to `_ANSWER_PROMPT` (the live chat page's choice); the
            eval harness can pass `_ANSWER_PROMPT_TIGHT` or another variant
            to A/B a prompt change without touching the live default.
        model: the Ollama model name passed to `generate_fn` -- defaults to
            `_LLM_MODEL` (`qwen2.5:3b`, the live chat page's choice);
            ignored outright when `generate_fn` is vLLM's client (it serves
            one fixed model regardless of this string). The eval harness
            can pass a different Ollama model to compare
            model size against the live default.
        enable_graph_tool: offers `lookup_graph_context` (Phase 2's
            knowledge-graph relationship tool) as a third choice when
            `True` -- defaults to `False` everywhere, so the live chat
            page's 2-tool behavior is unchanged until an eval run justifies
            flipping this default (see the Phase 2 plan).

    Returns:
        dict: `{"message_id": int (the persisted assistant ChatMessage's id
            -- what per-message thumbs up/down feedback links to, see
            app.chatbot.chat_history.save_feedback), "answer": str,
            "retrieved_chunks": list[dict] (each `{chunk_id, text,
            source_type, source_entity_id, tags, is_fresh}`, the
            traceability record), "tool_calls": list[dict]}`.
    """
    _tag_current_trace(session_id, role, question)
    chat_history.append_message(session, session_id, "user", question)

    # Input guardrail: runs before any LLM call, on the raw question --
    # deterministic, not a prompt instruction the model could ignore. See
    # app.chatbot.guardrails' module docstring. A blocked question never
    # reaches decide_tools/execute_tools/generate_answer at all.
    input_check = check_input_safety(question)
    if input_check.blocked:
        try:
            mlflow.update_current_trace(tags={"guardrail_triggered": input_check.reason})
        except Exception:
            pass
        assistant_message = chat_history.append_message(
            session, session_id, "assistant", input_check.decline_message, [],
        )
        return {
            "message_id": assistant_message.id, "answer": input_check.decline_message,
            "retrieved_chunks": [], "tool_calls": [], "tool_results": [],
        }

    if generate_fn is None:
        generate_fn = _resolve_generate_fn("ollama")
    history_text = _format_history(chat_history.load_recent_messages(session, session_id, limit=10))

    graph = _build_graph(
        session, generate_fn, model=model, answer_prompt=answer_prompt, enable_graph_tool=enable_graph_tool,
    )
    initial_state: ChatTurnState = {
        "question": question, "history_text": history_text, "role": role,
        "tool_calls": [], "tool_results": [], "retrieved_chunks": [], "answer": "",
    }
    final_state = graph.invoke(initial_state)

    chunk_ids = [c["chunk_id"] for c in final_state["retrieved_chunks"]]
    assistant_message = chat_history.append_message(
        session, session_id, "assistant", final_state["answer"], chunk_ids,
    )
    return {
        "message_id": assistant_message.id,
        "answer": final_state["answer"],
        "retrieved_chunks": final_state["retrieved_chunks"],
        "tool_calls": final_state["tool_calls"],
        "tool_results": final_state["tool_results"],
    }


def run_retrieval_turn(
    session: Session, role: str, question: str, generate_fn: GenerateFn | None = None,
    model: str = _LLM_MODEL, enable_graph_tool: bool = False,
) -> dict:
    """Thin wrapper -- see `run_chat_turn`'s wrapper docstring for why this
    split exists; delegates to `_run_retrieval_turn` for the real
    docstring/behavior."""
    _set_mlflow_experiment(_MLFLOW_RETRIEVAL_EXPERIMENT)
    return _run_retrieval_turn(
        session, role, question, generate_fn=generate_fn, model=model, enable_graph_tool=enable_graph_tool,
    )


@mlflow.trace(span_type=SpanType.CHAIN)
def _run_retrieval_turn(
    session: Session, role: str, question: str, generate_fn: GenerateFn | None = None,
    model: str = _LLM_MODEL, enable_graph_tool: bool = False,
) -> dict:
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
    _tag_current_trace(None, role, question)
    if generate_fn is None:
        generate_fn = _resolve_generate_fn("ollama")
    state: ChatTurnState = {
        "question": question, "history_text": "", "role": role,
        "tool_calls": [], "tool_results": [], "retrieved_chunks": [], "answer": "",
    }
    state = _decide_tools(generate_fn, model, enable_graph_tool, state)
    state = _execute_tools(session, state)
    return {
        "retrieved_chunks": state["retrieved_chunks"],
        "tool_calls": state["tool_calls"],
        "tool_results": state["tool_results"],
    }
