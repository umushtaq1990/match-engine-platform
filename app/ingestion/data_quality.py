"""Data-quality checks over the source tables.

Run with: python -m app.ingestion.data_quality

Each check is a pure function: (connection) -> CheckOutcome. Thresholds are
initial, documented judgment calls (see docs/data-model.md /
docs/assumptions-register.md), not objectively "correct" — revisit them once
real usage data exists. Results are written to data_quality_runs /
data_quality_check_results so they can be charted over time (Phase 9
dashboard), not just printed once and discarded.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db import SessionLocal, engine
from app.ingestion.quality_models import CheckStatus, DataQualityCheckResult, DataQualityRun


@dataclass
class CheckOutcome:
    check_name: str
    category: str
    status: CheckStatus
    actual_value: float | None
    threshold_warn: float | None = None
    threshold_fail: float | None = None
    affected_rows: int | None = None
    details: str | None = None


def _status_from_rate(rate: float, warn: float, fail: float) -> CheckStatus:
    if rate >= fail:
        return CheckStatus.fail
    if rate >= warn:
        return CheckStatus.warn
    return CheckStatus.pass_


def _status_from_count(count: int) -> CheckStatus:
    return CheckStatus.fail if count > 0 else CheckStatus.pass_


# --- Completeness ------------------------------------------------------------

def check_candidate_occupation_completeness(conn: Connection) -> CheckOutcome:
    total = conn.execute(text("SELECT count(*) FROM candidates")).scalar_one()
    missing = conn.execute(
        text("SELECT count(*) FROM candidates WHERE primary_occupation_id IS NULL")
    ).scalar_one()
    rate = missing / total if total else 0.0
    warn, fail = 0.10, 0.20
    return CheckOutcome(
        "candidate_occupation_completeness", "completeness",
        _status_from_rate(rate, warn, fail), rate, warn, fail, missing,
        f"{missing}/{total} candidates missing primary_occupation_id",
    )


def check_candidate_salary_completeness(conn: Connection) -> CheckOutcome:
    total = conn.execute(text("SELECT count(*) FROM candidates")).scalar_one()
    missing = conn.execute(
        text("SELECT count(*) FROM candidates WHERE desired_salary_min IS NULL")
    ).scalar_one()
    rate = missing / total if total else 0.0
    warn, fail = 0.25, 0.40
    return CheckOutcome(
        "candidate_salary_completeness", "completeness",
        _status_from_rate(rate, warn, fail), rate, warn, fail, missing,
        f"{missing}/{total} candidates missing desired_salary_min",
    )


def check_job_salary_completeness(conn: Connection) -> CheckOutcome:
    total = conn.execute(text("SELECT count(*) FROM jobs")).scalar_one()
    missing = conn.execute(
        text("SELECT count(*) FROM jobs WHERE salary_min IS NULL")
    ).scalar_one()
    rate = missing / total if total else 0.0
    warn, fail = 0.20, 0.35
    return CheckOutcome(
        "job_salary_completeness", "completeness",
        _status_from_rate(rate, warn, fail), rate, warn, fail, missing,
        f"{missing}/{total} jobs missing salary_min",
    )


# --- Uniqueness ---------------------------------------------------------------

def check_duplicate_candidate_emails(conn: Connection) -> CheckOutcome:
    dupes = conn.execute(
        text("SELECT count(*) FROM (SELECT email FROM candidates GROUP BY email HAVING count(*) > 1) d")
    ).scalar_one()
    return CheckOutcome(
        "duplicate_candidate_emails", "uniqueness",
        _status_from_count(dupes), float(dupes), affected_rows=dupes,
        details=f"{dupes} email values used by more than one candidate",
    )


def check_duplicate_candidate_identity(conn: Connection) -> CheckOutcome:
    """Candidates with identical name+location — plausible accidental
    duplicates, not a hard schema violation (hence informational/warn only)."""
    dupes = conn.execute(
        text(
            "SELECT count(*) FROM ("
            "  SELECT full_name, location_city, location_region FROM candidates"
            "  GROUP BY full_name, location_city, location_region HAVING count(*) > 1"
            ") d"
        )
    ).scalar_one()
    status = CheckStatus.warn if dupes > 0 else CheckStatus.pass_
    return CheckOutcome(
        "duplicate_candidate_identity", "uniqueness",
        status, float(dupes), affected_rows=dupes,
        details=f"{dupes} (name, city, region) combinations shared by more than one candidate",
    )


# --- Referential integrity ----------------------------------------------------

def check_orphan_candidate_skills(conn: Connection) -> CheckOutcome:
    orphans = conn.execute(
        text(
            "SELECT count(*) FROM candidate_skills cs "
            "LEFT JOIN candidates c ON c.id = cs.candidate_id "
            "LEFT JOIN skills s ON s.id = cs.skill_id "
            "WHERE c.id IS NULL OR s.id IS NULL"
        )
    ).scalar_one()
    return CheckOutcome(
        "orphan_candidate_skills", "referential_integrity",
        _status_from_count(orphans), float(orphans), affected_rows=orphans,
        details=f"{orphans} candidate_skills rows referencing a missing candidate or skill",
    )


def check_orphan_job_skills(conn: Connection) -> CheckOutcome:
    orphans = conn.execute(
        text(
            "SELECT count(*) FROM job_skills js "
            "LEFT JOIN jobs j ON j.id = js.job_id "
            "LEFT JOIN skills s ON s.id = js.skill_id "
            "WHERE j.id IS NULL OR s.id IS NULL"
        )
    ).scalar_one()
    return CheckOutcome(
        "orphan_job_skills", "referential_integrity",
        _status_from_count(orphans), float(orphans), affected_rows=orphans,
        details=f"{orphans} job_skills rows referencing a missing job or skill",
    )


# --- Range / validity ----------------------------------------------------------

def check_salary_range_consistency(conn: Connection) -> CheckOutcome:
    bad = conn.execute(
        text(
            "SELECT "
            "(SELECT count(*) FROM candidates WHERE desired_salary_min IS NOT NULL "
            " AND desired_salary_max IS NOT NULL AND desired_salary_min > desired_salary_max) + "
            "(SELECT count(*) FROM jobs WHERE salary_min IS NOT NULL "
            " AND salary_max IS NOT NULL AND salary_min > salary_max)"
        )
    ).scalar_one()
    return CheckOutcome(
        "salary_range_consistency", "range_validity",
        _status_from_count(bad), float(bad), affected_rows=bad,
        details=f"{bad} rows (candidates+jobs) where min salary exceeds max salary",
    )


def check_years_experience_range(conn: Connection) -> CheckOutcome:
    bad = conn.execute(
        text("SELECT count(*) FROM candidates WHERE years_experience < 0 OR years_experience > 50")
    ).scalar_one()
    return CheckOutcome(
        "years_experience_range", "range_validity",
        _status_from_count(bad), float(bad), affected_rows=bad,
        details=f"{bad} candidates with years_experience outside [0, 50]",
    )


# --- Taxonomy coverage ----------------------------------------------------------

def check_unused_skills(conn: Connection) -> CheckOutcome:
    """Skills in the canonical taxonomy never referenced anywhere — informational
    coverage metric, not a failure (a brand-new taxonomy entry is legitimately
    unused until data catches up)."""
    total = conn.execute(text("SELECT count(*) FROM skills")).scalar_one()
    unused = conn.execute(
        text(
            "SELECT count(*) FROM skills s WHERE NOT EXISTS ("
            "  SELECT 1 FROM candidate_skills cs WHERE cs.skill_id = s.id"
            "  UNION SELECT 1 FROM job_skills js WHERE js.skill_id = s.id"
            "  UNION SELECT 1 FROM occupation_skills os WHERE os.skill_id = s.id"
            "  UNION SELECT 1 FROM course_skills cos WHERE cos.skill_id = s.id"
            ")"
        )
    ).scalar_one()
    rate = unused / total if total else 0.0
    status = CheckStatus.warn if rate > 0.10 else CheckStatus.pass_
    return CheckOutcome(
        "unused_skills_coverage", "taxonomy_coverage",
        status, rate, threshold_warn=0.10, affected_rows=unused,
        details=f"{unused}/{total} canonical skills never referenced by any candidate/job/occupation/course",
    )


ALL_CHECKS = [
    check_candidate_occupation_completeness,
    check_candidate_salary_completeness,
    check_job_salary_completeness,
    check_duplicate_candidate_emails,
    check_duplicate_candidate_identity,
    check_orphan_candidate_skills,
    check_orphan_job_skills,
    check_salary_range_consistency,
    check_years_experience_range,
    check_unused_skills,
]


def run_all_checks() -> tuple[DataQualityRun, list[CheckOutcome]]:
    session = SessionLocal()
    with engine.connect() as conn:
        outcomes = [check(conn) for check in ALL_CHECKS]

    run = DataQualityRun(
        run_at=datetime.now(timezone.utc),
        total_checks=len(outcomes),
        passed_checks=sum(1 for o in outcomes if o.status == CheckStatus.pass_),
        warned_checks=sum(1 for o in outcomes if o.status == CheckStatus.warn),
        failed_checks=sum(1 for o in outcomes if o.status == CheckStatus.fail),
    )
    session.add(run)
    session.flush()

    for o in outcomes:
        session.add(
            DataQualityCheckResult(
                run_id=run.id,
                check_name=o.check_name,
                category=o.category,
                status=o.status,
                threshold_warn=o.threshold_warn,
                threshold_fail=o.threshold_fail,
                actual_value=o.actual_value,
                affected_rows=o.affected_rows,
                details=o.details,
            )
        )

    session.commit()
    session.refresh(run)
    session.close()
    return run, outcomes


if __name__ == "__main__":
    run, outcomes = run_all_checks()
    print(f"Data quality run #{run.id} at {run.run_at}: "
          f"{run.passed_checks} passed, {run.warned_checks} warned, {run.failed_checks} failed")
    for o in outcomes:
        print(f"  [{o.status.value.upper():4}] {o.check_name}: {o.details}")
