"""record_change (issue #24) -- the one write path every edit-history caller
goes through, so "only log what actually changed" is enforced in one place
rather than re-implemented per route.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.models import EntityChangeLog


def record_change(
    db: Session, entity_type: str, entity_id: int, changed_by: str | None,
    old: dict, new: dict,
) -> None:
    diff = {key: {"old": old.get(key), "new": value} for key, value in new.items() if old.get(key) != value}
    if not diff:
        return
    db.add(
        EntityChangeLog(
            entity_type=entity_type, entity_id=entity_id, changed_by_keycloak_user_id=changed_by,
            changed_at=datetime.now(timezone.utc), changes=diff,
        )
    )
