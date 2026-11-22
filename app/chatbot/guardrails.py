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
"""
import re
from dataclasses import dataclass

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

_PII_PATTERNS = (_EMAIL_RE, _ADDRESS_RE, _PHONE_RE, _POSTAL_CODE_RE)

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
