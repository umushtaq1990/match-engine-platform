"""One-off bootstrap: creates a single admin-role Keycloak user who can see
`"confidential"` chatbot content. Not a self-serve admin UI -- proportionate
for a single-developer platform, same manual-step discipline the plan this
shipped from calls for. Run once: `python -m app.chatbot.provision_admin`.

Reuses `app.frontend.keycloak_admin`'s existing Admin-API client unmodified
(the same one `app.golden.provision_logins` already uses for golden
candidates) -- this just assigns the new `"admin"` realm role
(docker/keycloak/jme-realm.json) instead of `"candidate"`/`"employer"`.
"""
import argparse

from app.frontend.keycloak_admin import assign_realm_role, create_user, get_admin_token

DEFAULT_EMAIL = "chatbot-admin@example-synthetic.test"
DEFAULT_PASSWORD = "chatbot-admin-dev-pw"


def main(email: str, password: str) -> None:
    admin_token = get_admin_token()
    user_id = create_user(email, "Chatbot Admin", password, admin_token)
    assign_realm_role(user_id, "admin", admin_token)
    print(f"Created admin user {email!r} (id={user_id}). Sign in on the Chatbot page with this password.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()
    main(args.email, args.password)
