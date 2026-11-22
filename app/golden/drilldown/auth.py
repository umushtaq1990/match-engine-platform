"""Lightweight login gate for the Chatbot page only (app/golden/drilldown/
pages/9_Chatbot.py) -- the other 7 drilldown pages stay exactly as they
were (no login), a deliberately narrow change rather than retrofitting
auth onto an already-working internal tool. Reuses the existing Keycloak
password-grant flow (`app.frontend.oidc`) unmodified, not a new auth
mechanism.

Role comes from the ID token's `realm_access.roles` claim directly (the
Keycloak realm-role mechanism `app.frontend.keycloak_admin.assign_realm_role`
already writes to), not from the Postgres `UserProfile`/candidate-employer
onboarding flow `app.frontend.streamlit_app._require_profile` uses -- an
admin user of this internal tool isn't registering a candidate/employer
profile, so that flow doesn't fit here.
"""
import streamlit as st

from app.chatbot.access_control import ROLE_ADMIN, ROLE_CANDIDATE, ROLE_EMPLOYER
from app.frontend.oidc import decode_id_token, password_grant_login


def _role_from_claims(claims: dict) -> str:
    roles = claims.get("realm_access", {}).get("roles", [])
    if ROLE_ADMIN in roles:
        return ROLE_ADMIN
    if ROLE_EMPLOYER in roles:
        return ROLE_EMPLOYER
    if ROLE_CANDIDATE in roles:
        return ROLE_CANDIDATE
    return ROLE_CANDIDATE  # most restrictive default for an unrecognized/absent role


def require_login() -> tuple[str, str]:
    """Shows a login form until the user authenticates, then returns their
    identity. Call this first in the Chatbot page, before anything else
    renders.

    Returns:
        tuple[str, str]: `(user_id, role)` -- `user_id` is the token's
            `sub` claim (a stable Keycloak UUID), `role` is one of
            `app.chatbot.access_control`'s role constants.
    """
    if "chatbot_claims" in st.session_state:
        claims = st.session_state["chatbot_claims"]
        return claims["sub"], _role_from_claims(claims)

    st.title("Chatbot -- sign in")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Sign in"):
        try:
            tokens = password_grant_login(username, password)
        except ValueError:
            st.error("Invalid username or password.")
            st.stop()
        claims = decode_id_token(tokens["id_token"])
        st.session_state["chatbot_claims"] = claims
        st.rerun()
    st.stop()
