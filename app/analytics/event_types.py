"""Event taxonomy (Phase 14) — a plain Python set, not a Postgres ENUM.

Keeping this in application code means adding a new event type is a one-line
change here, not an Alembic migration — see docs/behavioral-analytics.md
"Event schema (v1)".
"""

ACTOR_TYPES = {"candidate", "employer"}

ENTITY_TYPES = {"job", "candidate", "course"}

EVENT_TYPES = {
    "recommendation_shown",
    "recommendation_clicked",
    "job_applied",
    "job_saved",
    "search_performed",
    "login",
}
