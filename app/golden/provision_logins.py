"""Provisions a real Keycloak login for each of the 500 golden candidates
(app/golden/select.py), reusing the exact same Admin-API client
(app/frontend/keycloak_admin.py) that backs the real inline self-registration
form -- `create_user` already splits `full_name` into firstName/lastName,
which is what satisfies Keycloak 26's VERIFY_PROFILE required action (see
the CI debugging this session already did for the throwaway CI test user --
this reuse sidesteps that pitfall entirely rather than re-deriving it).

Email/password are fully deterministic from candidate_id -- no credential
manifest file needs to be generated or stored; any later script (a live
traffic simulator, a demo login) can recompute both from the id alone, same
spirit as the realm's existing checked-in dev-mode demo passwords
(candidate-dev-pw/employer-dev-pw).

Run with: docker compose run --rm golden-logins [--limit N]

Idempotent/resumable: skips any candidate_id that already has a
UserProfile.candidate_id row.
"""
import argparse
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.frontend.keycloak_admin import assign_realm_role, create_user, get_admin_token
from app.golden.select import select_golden_candidate_ids
from app.identity.models import UserProfile, UserRole
from app.ingestion.source_models import Candidate

GOLDEN_PASSWORD = "golden-dev-pw"


def golden_email(candidate_id: int) -> str:
    return f"golden-candidate-{candidate_id}@example-synthetic.test"


def main(limit: int | None) -> None:
    session = SessionLocal()
    try:
        golden_ids = select_golden_candidate_ids(session)
        if limit is not None:
            golden_ids = golden_ids[:limit]

        already_linked = {
            row[0] for row in session.execute(
                select(UserProfile.candidate_id).where(UserProfile.candidate_id.isnot(None))
            ).all()
        }

        admin_token = get_admin_token()
        created = 0
        skipped = 0
        for i, candidate_id in enumerate(golden_ids, start=1):
            if candidate_id in already_linked:
                skipped += 1
                continue

            candidate = session.get(Candidate, candidate_id)
            email = golden_email(candidate_id)
            try:
                user_id = create_user(email, candidate.full_name, GOLDEN_PASSWORD, admin_token)
            except ValueError:
                # Already exists in Keycloak from a prior partial run -- the
                # UserProfile link is what we're actually missing, so we
                # can't recover the user_id from create_user's response;
                # skip and let a future targeted fix handle this rare case.
                print(
                    f"[{i}/{len(golden_ids)}] candidate {candidate_id}: "
                    "Keycloak user already exists, skipping"
                )
                skipped += 1
                continue

            assign_realm_role(user_id, "candidate", admin_token)

            session.add(UserProfile(
                keycloak_user_id=user_id, role=UserRole.candidate,
                full_name=candidate.full_name, email=email, candidate_id=candidate_id,
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()
            created += 1
            print(f"[{i}/{len(golden_ids)}] candidate {candidate_id}: Keycloak login provisioned ({email})")

        print(f"Golden logins provisioned: {created}, skipped (already linked): {skipped}")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N golden candidates")
    args = parser.parse_args()
    main(limit=args.limit)
