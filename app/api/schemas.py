"""Pydantic response models for the matching API.

Deliberately separate from the dataclasses in app/matching/ — those are the
internal computation shapes (sets, used for set algebra in scoring.py); these
are the wire format (sorted lists, JSON-safe), so the two can evolve
independently without breaking API consumers.
"""
from pydantic import BaseModel


class ScoreOut(BaseModel):
    skill: float
    location: float
    salary: float
    occupation: float
    total: float


class SkillGapOut(BaseModel):
    confirmed_absent_required: list[int]
    not_recorded_required: list[int]
    confirmed_absent_preferred: list[int]
    not_recorded_preferred: list[int]


class MatchOut(BaseModel):
    entity_id: int
    label: str
    eligible: bool
    ineligible_reason: str | None
    score: ScoreOut
    skill_gap: SkillGapOut
    explanation: str


class OccupationSkillGapOut(BaseModel):
    occupation_id: int
    present_skill_ids: list[int]
    confirmed_absent_skill_ids: list[int]
    not_recorded_skill_ids: list[int]
    total_gap_weight: float


class CourseRecommendationOut(BaseModel):
    course_id: int
    title: str
    coverage_score: float
    covered_skill_ids: list[int]


class CourseRecommendationsResponse(BaseModel):
    skill_gap: OccupationSkillGapOut
    recommendations: list[CourseRecommendationOut]


class SemanticMatchOut(BaseModel):
    """Phase 17: embeddings-blended match — a complementary signal to
    MatchOut's rule-based score, not a replacement. See
    docs/embedding-matching.md.
    """
    job_id: int
    total_score: float
    skills_similarity: float | None
    experience_similarity: float | None
    overall_similarity: float | None
    location_score: float
    salary_score: float
    occupation_score: float
