"""Input/output guardrails for the chat agent -- deterministic, regex/rule
based, deliberately NOT delegated to the LLM. Two reasons this exists as
its own layer instead of just relying on `_BOT_SCOPE`'s prompt instructions
or on the tool layer already omitting PII columns (`lookup_structured_data`
never selects `Candidate.email`/`postal_code`/etc today):

1. Prompt instructions are a request to a small, sometimes-hallucinating
   model, not a guarantee -- live testing this session already showed it
   inventing facts (specific skill names) that weren't in a tool's result.
   A model that will hallucinate a skill will hallucinate a phone number
   just as easily if ever asked persistently enough or if future ingestion
   adds a document containing one.
2. Defense in depth against the data model changing -- if a future
   ingestion pass ever puts raw resume text (which *does* routinely contain
   emails/phone numbers/addresses) into a `DocumentChunk`, this layer catches
   it even if nobody remembers to re-review every prompt at that time.

Both directions are covered:
- `check_input_safety`: runs on the raw user question, before any LLM call.
  Flags prompt-injection attempts and direct requests for a candidate's
  contact details, and returns a canned decline instead of invoking the
  model at all for those cases -- deterministic, not a "please don't"
  instruction the model could ignore.
- `redact_pii`: runs on tool results (before they enter the answer-
  generation prompt) and on the final generated answer (before it's shown
  or persisted) -- strips anything that matches an email/phone/street-
  address/postal-code shape, regardless of where it came from or whether
  the model echoed or invented it.
- `check_rate_limit`: a third, separate concern from PII/injection --
  bounds how many questions one user can ask per minute, backed by a
  Postgres query over `ChatMessage`/`ChatSession` rather than an in-memory
  counter. An in-memory counter resets on every restart and doesn't see
  requests landing on a different worker process/container, so it would
  silently stop enforcing anything in a real multi-instance deployment;
  the DB-backed count is correct regardless of how many app instances are
  running.
- `chat_turn_capacity_slot`/`LLM_UNAVAILABLE_MESSAGE`: a fourth concern --
  this is a single Ollama instance (`OLLAMA_NUM_PARALLEL=5` in
  docker-compose.yml) with no load balancer or second replica in front of
  it, because the host it runs on has no spare GPU/RAM for a second copy.
  That's a real, honestly-documented limit (see
  docs/architecture/application-services/rag-chatbot.md), not something
  this layer pretends to fix. What it *does* fix is turning an overload
  into a clean decline instead of either behavior a caller would otherwise
  see: a burst of concurrent requests beyond what Ollama can actually run
  at once queuing silently (growing latency with no user-facing signal),
  or Ollama being unreachable/erroring crashing the Streamlit page with a
  raw traceback. `chat_turn_capacity_slot` bounds in-flight chat turns to
  match `OLLAMA_NUM_PARALLEL`, and `_run_chat_turn` wraps the actual LLM
  call in a `try`/`except` so a connection failure or timeout becomes a
  canned decline (tagged on the trace like every other guardrail) rather
  than an unhandled exception.
"""
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chatbot.models import ChatMessage, ChatSession

_REDACTED = "[redacted]"

# Deliberately conservative (favors missing an edge case over mangling
# ordinary text like "shared 3 skills" or "candidate #42"): each pattern
# requires enough structure that normal chatbot output won't match it.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
# 7+ digits, allowing separators -- long enough to rule out plain small
# numbers/ids, loose enough to catch "+1 555-123-4567", "(020) 7946 0958".
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?(?:\(\d{2,4}\)[-.\s]?)?\d{2,4}[-.\s]\d{3,4}[-.\s]?\d{3,4}")
_STREET_SUFFIXES = (
    r"Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Place|Pl|Way|Terrace"
)
_ADDRESS_RE = re.compile(
    rf"\b\d{{1,5}}\s+[A-Za-z0-9.'\s]{{2,30}}\b(?:{_STREET_SUFFIXES})\b\.?", re.IGNORECASE,
)
# US-style 5(-4) zip or UK-style postcode shape -- only matched standalone
# (word-boundaried), so it won't catch an ordinary 5-digit id.
_POSTAL_CODE_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b|\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b")
# Catches "john dot smith at example dot com" / "jane [dot] doe [at] example
# [dot] com" -- a plain-text obfuscation someone would type specifically to
# dodge `_EMAIL_RE`'s literal "@"/"." requirement. Evaluated and rejected
# Presidio (see rag-chatbot.md's PII hardening section) before writing this:
# its EMAIL_ADDRESS/PHONE_NUMBER recognizers didn't catch this either (same
# literal-character assumption as our own regex) -- a real gap neither
# approach closed until this pattern was added for it specifically.
_OBFUSCATED_EMAIL_RE = re.compile(
    r"\b[\w.+-]+(?:\s+dot\s+|\s*\[dot\]\s*|\s*\(dot\)\s*)[\w-]+(?:\s+at\s+|\s*\[at\]\s*|\s*\(at\)\s*)"
    r"[\w-]+(?:\s+dot\s+|\s*\[dot\]\s*|\s*\(dot\)\s*)[a-zA-Z]{2,}\b",
    re.IGNORECASE,
)
# US SSN shape (###-##-####) -- a distinct enough grouping that it won't
# collide with this schema's own id formats (plain integers, never
# dash-grouped) in practice, even though a deliberately adversarial fake id
# in that exact shape could still trip it -- the same "favor catching a real
# SSN over protecting a hypothetical id format this schema doesn't use"
# tradeoff the other patterns in this module already make.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

_PII_PATTERNS = (_EMAIL_RE, _OBFUSCATED_EMAIL_RE, _ADDRESS_RE, _PHONE_RE, _POSTAL_CODE_RE, _SSN_RE)

# Phrases that indicate the question is trying to get the model to ignore
# its own instructions/scope rather than ask a real recruiting-data
# question -- checked against the raw question, case-insensitively.
_INJECTION_PATTERNS = (
    re.compile(r"ignore (?:all |any )?(?:previous|prior|above)?\s*instructions", re.IGNORECASE),
    re.compile(
        r"disregard (?:the |your )?(?:previous|prior|system)?\s*(?:instructions|prompt)", re.IGNORECASE,
    ),
    re.compile(r"you are now\b", re.IGNORECASE),
    re.compile(r"reveal (?:your |the )?(?:system )?prompt", re.IGNORECASE),
    re.compile(r"act as (?:if|a)\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"\bDAN\b"),
)

# A direct ask for a contact detail -- caught before any tool call, since
# today's tools never return these fields anyway and a canned decline is
# more honest (and cheaper) than running the model only to have it decline.
_CONTACT_DETAIL_RE = re.compile(
    r"\b(email address|email|e-mail|phone number|phone|telephone|mobile number|cell number|"
    r"home address|mailing address|postal address|street address|where (?:does|do) .* live)\b",
    re.IGNORECASE,
)
_CONTACT_SUBJECT_RE = re.compile(r"\bcandidate\b|\bcandidates\b", re.IGNORECASE)

INJECTION_DECLINE_MESSAGE = (
    "I can't follow instructions embedded in a question -- I only answer using this platform's "
    "candidate/job/skill/course data. Please rephrase as a plain question about that data."
)
CONTACT_DETAIL_DECLINE_MESSAGE = (
    "I can't share personal contact details (email, phone, or address) for any candidate -- that's "
    "restricted to protect candidate privacy, regardless of role. I can help with their skills, "
    "experience, or job/course matches instead."
)


@dataclass
class InputCheckResult:
    """Attributes:
    blocked: `True` if the question should be declined outright, without
        ever reaching the LLM.
    reason: a short machine-readable tag (`"injection"` /
        `"contact_detail_request"`), for tagging the MLflow trace -- `None`
        when `blocked` is `False`.
    decline_message: the canned response to use instead -- `None` when
        `blocked` is `False`.
    """

    blocked: bool
    reason: str | None = None
    decline_message: str | None = None


def check_input_safety(question: str) -> InputCheckResult:
    """The input guardrail -- call on the raw user question, before
    `decide_tools`. See module docstring for why this exists instead of
    trusting the model to self-censor.
    """
    if any(pattern.search(question) for pattern in _INJECTION_PATTERNS):
        return InputCheckResult(blocked=True, reason="injection", decline_message=INJECTION_DECLINE_MESSAGE)
    if _CONTACT_DETAIL_RE.search(question) and _CONTACT_SUBJECT_RE.search(question):
        return InputCheckResult(
            blocked=True, reason="contact_detail_request", decline_message=CONTACT_DETAIL_DECLINE_MESSAGE,
        )
    return InputCheckResult(blocked=False)


def redact_pii(text: str) -> tuple[str, bool]:
    """The output guardrail -- call on anything about to be shown to the
    user or fed into the answer-generation prompt: tool results and the
    final generated answer alike.

    Returns:
        tuple[str, bool]: the text with any email/phone/address/postal-code
            shaped substring replaced by `"[redacted]"`, and whether
            anything was actually redacted (so a caller can tag the trace).
    """
    redacted = False
    for pattern in _PII_PATTERNS:
        new_text, count = pattern.subn(_REDACTED, text)
        if count:
            redacted = True
            text = new_text
    return text, redacted


# Generous enough not to interrupt real interactive use (the slowest
# single-question round trip seen live is ~25s, so a person can't
# naturally exceed this), tight enough to stop a scripted scrape/abuse
# loop -- a judgment call, not a measured threshold; revisit if real
# usage patterns show otherwise.
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_WINDOW_SECONDS = 60

RATE_LIMIT_DECLINE_MESSAGE = (
    "You've sent a lot of messages in a short time -- please wait a moment before asking again. "
    "This limit protects the service for everyone."
)


@dataclass
class RateLimitResult:
    """Attributes:
    blocked: `True` if this user has hit `RATE_LIMIT_MAX_REQUESTS` within
        `RATE_LIMIT_WINDOW_SECONDS`.
    request_count: how many user messages this user has sent across all
        their sessions within the window -- included for the trace tag/log,
        not just a bare bool.
    """

    blocked: bool
    request_count: int


def check_rate_limit(
    session: Session, session_id: int,
    max_requests: int = RATE_LIMIT_MAX_REQUESTS, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
) -> RateLimitResult:
    """Counts this user's own messages (by `ChatSession.user_id`, not just
    this one `session_id` -- a user opening a second browser tab gets a
    second `ChatSession` row but is still one person who can exhaust the
    same limit) across all their sessions in the trailing `window_seconds`.
    Call with the *new* message already persisted (see `_run_chat_turn`),
    so the count includes the request being checked.
    """
    chat_session = session.get(ChatSession, session_id)
    if chat_session is None:
        return RateLimitResult(blocked=False, request_count=0)
    since = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    count = session.execute(
        select(func.count(ChatMessage.id))
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatSession.user_id == chat_session.user_id,
            ChatMessage.role == "user",
            ChatMessage.created_at >= since,
        )
    ).scalar_one()
    return RateLimitResult(blocked=count > max_requests, request_count=count)


def redact_pii_in_value(value):
    """Recursively applies `redact_pii` to every string inside a JSON-like
    structure (dict/list/str) -- tool results are nested dicts/lists, not
    flat strings, so a single top-level `redact_pii` call would miss
    strings buried in them. Non-string leaves (ints, None, bools) pass
    through unchanged.

    Returns:
        tuple[Any, bool]: the redacted structure, and whether anything in
            it was redacted.
    """
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        any_redacted = False
        result = {}
        for key, inner in value.items():
            new_inner, inner_redacted = redact_pii_in_value(inner)
            result[key] = new_inner
            any_redacted = any_redacted or inner_redacted
        return result, any_redacted
    if isinstance(value, list):
        any_redacted = False
        result = []
        for inner in value:
            new_inner, inner_redacted = redact_pii_in_value(inner)
            result.append(new_inner)
            any_redacted = any_redacted or inner_redacted
        return result, any_redacted
    return value, False


# Matches docker-compose.yml's `OLLAMA_NUM_PARALLEL: "5"` -- letting more
# chat turns than that run at once wouldn't make them faster (Ollama's own
# serving layer is the bottleneck, not this process), it would just mean
# more requests silently queued behind Ollama with no indication to the
# person waiting on each one. One process-wide semaphore is correct here
# because the live chat page runs as a single Streamlit process against a
# single Ollama instance -- this is NOT a substitute for real horizontal
# scaling (a second Ollama/vLLM replica behind a load balancer), it only
# makes this one instance's overload behavior honest instead of silent.
MAX_CONCURRENT_CHAT_TURNS = 5
_capacity_semaphore = threading.Semaphore(MAX_CONCURRENT_CHAT_TURNS)

CAPACITY_DECLINE_MESSAGE = (
    "The assistant is handling a lot of questions right now -- please try again in a few seconds."
)
LLM_UNAVAILABLE_MESSAGE = (
    "I couldn't reach the language model backend just now -- please try again shortly. If this "
    "keeps happening, let an administrator know."
)


@contextmanager
def chat_turn_capacity_slot() -> Iterator[bool]:
    """Non-blocking: a caller that doesn't get a slot should decline the
    turn immediately (see `CAPACITY_DECLINE_MESSAGE`), not queue silently
    behind `MAX_CONCURRENT_CHAT_TURNS` other in-flight turns. Yields
    whether a slot was acquired; releases it on exit either way (a no-op
    when it was never acquired).
    """
    acquired = _capacity_semaphore.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _capacity_semaphore.release()
