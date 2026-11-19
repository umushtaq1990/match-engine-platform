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

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
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


class GoldenInjectedNoise(Base):
    """golden-dataset v2: tracks exactly which golden candidates/jobs got a
    deliberately wrong skill added to their ground truth (CandidateSkill/
    JobSkill) *after* their CV/job text was already written -- app/golden/
    inject_noise.py. Polymorphic by which FK is set (exactly one of
    candidate_id/job_id, never both/neither), and deliberately independent
    of each other: a candidate-noise row only affects that candidate's own
    CandidateSkill ground truth (what their CV is scored against); a
    job-noise row only affects that one specific job's JobSkill ground
    truth. Noise is NOT propagated from a candidate onto their 3 paired
    jobs (or vice versa) -- each example's ground truth is corrupted (or
    not) independently, so later analysis can tell the two failure modes
    apart. This table is the ground truth a LATER pass (not built here)
    needs to check whether extraction/the review-queue confidence scoring
    (app/extraction/pipeline.py's _document_confidence) actually flags these
    as data-quality issues -- the skill is real (a valid skill_id), it's
    just genuinely not mentioned anywhere in that example's text by
    construction, so a correct extraction should never find it.
    """
    __tablename__ = "golden_injected_noise"
    __table_args__ = (
        CheckConstraint(
            "(candidate_id IS NOT NULL) != (job_id IS NOT NULL)",
            name="ck_golden_injected_noise_exactly_one_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), index=True, nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True, nullable=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    injected_at: Mapped[datetime] = mapped_column()


class GraphNarrative(Base):
    """Persisted cache for the Candidate/Job Profile 360 pages' "Knowledge
    graph position" LLM narrative (app/golden/drilldown/
    graph_explainability.py) -- durable, shared-across-replicas version of
    what started as an in-process `st.cache_data` cache. `entity_type`/
    `entity_id` is a free pair (not a FK), same polymorphic shape as
    app.embeddings.models.EntityEmbedding, since this caches both
    Candidate and Job narratives in one table rather than two near-
    identical ones.

    `facts_hash` is the real invalidation check: a sha256 of the exact
    `summarize_graph_facts(...)` output the stored narrative was generated
    from. A page load always recomputes facts fresh from Neo4j (cheap --
    the Cypher queries, not the LLM call, dominate neither this table's
    nor the overall request's cost) and only calls the LLM again if the
    hash no longer matches -- i.e. this candidate's/job's skills, peers,
    adjacent jobs, or courses actually changed since the row was written,
    not a time-based guess. `facts` itself is stored alongside for
    debugging/audit, not just the hash.
    """
    __tablename__ = "graph_narratives"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_graph_narrative_entity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    entity_id: Mapped[int] = mapped_column(index=True)
    facts_hash: Mapped[str] = mapped_column(String(64))
    facts: Mapped[dict] = mapped_column(JSONB)
    narrative_text: Mapped[str] = mapped_column(Text())
    generated_at: Mapped[datetime] = mapped_column()
