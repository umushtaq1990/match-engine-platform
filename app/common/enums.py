"""Enums shared by both the ORM layer (`app.ingestion.source_models`) and
pure, DB-free business logic (`app.matching.scoring`).

Why this file exists: `DegreeLevel` used to live in `app.ingestion.
source_models` alongside every other ORM-column enum. That made
`app.matching.scoring` — documented everywhere else as "pure functions only,
no DB, no I/O" — import from the ingestion/ORM package just to get an enum's
values for an ordinal comparison, the one place that module's own
"decoupled from ingestion" claim didn't hold. Moving the enum here means
both sides import from a neutral module that depends on neither of them,
so a project reusing `app.matching.scoring` on its own doesn't have to carry
`app.ingestion.source_models` (SQLAlchemy, a live `Base`, every other ORM
model) along with it just for one enum.

Add future enums here only if something in `app.matching`'s pure modules
needs their *values* (not just a column type) — an enum that only ever
backs an ORM column belongs in `source_models.py` with the rest.
"""
import enum


class DegreeLevel(str, enum.Enum):
    """Highest level of education — the one dimension of "education" issue
    #21 models. A plain `str` enum (this project's convention for every
    enum that round-trips through JSON/the API layer) rather than an
    `IntEnum`; ordinal comparison for scoring is a separate explicit
    ranking table in `app.matching.scoring`, not the enum's own order.
    """

    high_school = "high_school"
    associate = "associate"
    bachelor = "bachelor"
    master = "master"
    doctorate = "doctorate"
