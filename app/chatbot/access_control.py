"""Role-based document visibility -- the one gate standing between
`DocumentChunk.visibility` and a chat answer. Deliberately tiny: a single
dict, not a permissions framework, proportionate to this phase's two
visibility levels and three roles.
"""
from app.chatbot.models import VISIBILITY_CONFIDENTIAL, VISIBILITY_PUBLIC

ROLE_ADMIN = "admin"
ROLE_CANDIDATE = "candidate"
ROLE_EMPLOYER = "employer"

ALLOWED_VISIBILITY_BY_ROLE: dict[str, list[str]] = {
    ROLE_ADMIN: [VISIBILITY_PUBLIC, VISIBILITY_CONFIDENTIAL],
    ROLE_CANDIDATE: [VISIBILITY_PUBLIC],
    ROLE_EMPLOYER: [VISIBILITY_PUBLIC],
}


def allowed_visibilities(role: str) -> list[str]:
    """Which `DocumentChunk.visibility` values a role may retrieve.

    Args:
        role: the user's role, e.g. from the decoded ID token's
            `realm_access.roles` claim.

    Returns:
        list[str]: an unrecognized role gets the most restrictive answer
            (`[VISIBILITY_PUBLIC]`), not an exception -- a chatbot
            retrieval filter should fail closed, not fail loudly, if a
            role isn't one it recognizes.
    """
    return ALLOWED_VISIBILITY_BY_ROLE.get(role, [VISIBILITY_PUBLIC])
