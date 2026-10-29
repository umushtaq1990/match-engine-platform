"""ORM models for data-quality check results.

Separate from source_models.py: these tables are OUR output (an audit trail
we produce by evaluating the source data), not source-of-record data itself.
Still lives under app/ingestion/ because producing data-quality reports is
part of the ingestion/governance responsibility (brief Layer 2), distinct
from the matching engine's own derived recommendation data (Layer 5).
"""
import enum
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CheckStatus(str, enum.Enum):
    pass_ = "pass"
    warn = "warn"
    fail = "fail"


_check_status_values = Enum(
    CheckStatus, name="checkstatus", values_callable=lambda obj: [e.value for e in obj]
)


class DataQualityRun(Base):
    __tablename__ = "data_quality_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column()
    total_checks: Mapped[int] = mapped_column()
    passed_checks: Mapped[int] = mapped_column()
    warned_checks: Mapped[int] = mapped_column()
    failed_checks: Mapped[int] = mapped_column()

    results: Mapped[list["DataQualityCheckResult"]] = relationship(back_populates="run")


class DataQualityCheckResult(Base):
    __tablename__ = "data_quality_check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("data_quality_runs.id"), index=True)
    check_name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    status: Mapped[CheckStatus] = mapped_column(_check_status_values)
    threshold_warn: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    threshold_fail: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    affected_rows: Mapped[int | None] = mapped_column(nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["DataQualityRun"] = relationship(back_populates="results")
