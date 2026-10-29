"""ORM models for evaluation run results. Same two-level pattern as
app/ingestion/quality_models.py (run summary + per-item detail), so results
are historized and comparable across scoring-weight configurations over
time, not just printed once and discarded.
"""
from datetime import datetime

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column()
    k: Mapped[int] = mapped_column()
    sample_size: Mapped[int] = mapped_column()
    excluded_no_ground_truth: Mapped[int] = mapped_column()
    config_label: Mapped[str] = mapped_column(String(200))
    precision_at_k_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    recall_at_k_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    ndcg_at_k_avg: Mapped[float] = mapped_column(Numeric(6, 4))
    mrr_avg: Mapped[float] = mapped_column(Numeric(6, 4))

    candidate_results: Mapped[list["EvaluationCandidateResult"]] = relationship(
        back_populates="run"
    )


class EvaluationCandidateResult(Base):
    __tablename__ = "evaluation_candidate_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(index=True)
    precision_at_k: Mapped[float] = mapped_column(Numeric(6, 4))
    recall_at_k: Mapped[float] = mapped_column(Numeric(6, 4))
    ndcg_at_k: Mapped[float] = mapped_column(Numeric(6, 4))
    reciprocal_rank: Mapped[float] = mapped_column(Numeric(6, 4))

    run: Mapped["EvaluationRun"] = relationship(back_populates="candidate_results")
