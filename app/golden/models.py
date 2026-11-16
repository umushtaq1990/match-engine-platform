"""A dedicated, hand-curated golden evaluation dataset: 500 of the existing
seeded candidates (app/golden/select.py), each given a realistic LLM-written
CV and three LLM-written job postings deliberately constructed to be a
near-perfect skill match -- see docs/architecture (or the plan this shipped
from) for the full rationale. GoldenPair is the explicit, queryable mapping
from a candidate to their three paired jobs, used by the recommendation-eval
harness (app/golden/run_recommendation_eval.py) to check whether the matcher
actually ranks a candidate's *known* right answers highly, instead of only
the weaker "same occupation somewhere in the pool" ground truth
app/evaluation/ground_truth.py uses for the full population.
"""
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GoldenPair(Base):
    __tablename__ = "golden_pairs"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    # unique, not a composite PK with candidate_id -- one candidate maps to
    # three rows (three distinct golden jobs), but a golden job belongs to
    # exactly one candidate.
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column()
