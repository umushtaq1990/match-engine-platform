"""Pydantic response models for the matching API.

Deliberately separate from the dataclasses in app/matching/ — those are the
internal computation shapes (sets, used for set algebra in scoring.py); these
are the wire format (sorted lists, JSON-safe), so the two can evolve
independently without breaking API consumers.
"""
from pydantic import BaseModel, Field

from app.identity.models import UserRole


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


class ProfileCreate(BaseModel):
    """Issue #15: identity-profile registration payload — the Keycloak ID
    token's `sub` claim is trusted as given by the frontend, same trust
    boundary app/frontend/keycloak_admin.py's Admin-API calls already have.
    """
    keycloak_user_id: str
    role: UserRole
    full_name: str
    email: str
    company_name: str | None = None


class ProfileOut(BaseModel):
    model_config = {"from_attributes": True}

    keycloak_user_id: str
    role: UserRole
    full_name: str
    email: str
    company_name: str | None
    candidate_id: int | None  # issue #17: server-assigned, only set for role=candidate


class CandidateDetailsOut(BaseModel):
    """Issue #18: the scoring-relevant fields `app/matching/scoring.py` reads
    for a candidate (location, salary, occupation, skills) — editable by the
    candidate themself, as opposed to `ProfileOut`'s identity fields.
    """
    model_config = {"from_attributes": True}

    location_city: str
    postal_code: str | None
    country: str | None
    desired_salary_min: int | None
    desired_salary_max: int | None
    primary_occupation_id: int | None
    experience_level: str | None
    skill_ids: list[int]


class CandidateDetailsUpdate(BaseModel):
    """Issue #20: `location_region` is deliberately absent here -- it's still
    read by app/matching/scoring.py's _location_component, but the frontend
    no longer collects it from the user, so the PATCH handler leaves it
    untouched rather than overwriting it with nothing.
    """
    location_city: str
    postal_code: str | None = None
    country: str | None = None
    desired_salary_min: int | None = None
    desired_salary_max: int | None = None
    primary_occupation_id: int | None = None
    experience_level: str | None = None
    skill_ids: list[int] = Field(default_factory=list, max_length=10)


class OccupationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str


class OccupationCreate(BaseModel):
    title: str


class SkillOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str


class SkillCreate(BaseModel):
    name: str


class JobCreate(BaseModel):
    """Issue #22: an employer-posted job, owned by the posting Keycloak user
    (trusted as given by the frontend, same boundary as ProfileCreate above).
    """
    posted_by_keycloak_user_id: str
    title: str
    company_name: str
    description: str
    location_city: str
    location_region: str
    salary_min: int | None = None
    salary_max: int | None = None
    primary_occupation_id: int | None = None
    experience_level: str | None = None
    required_skill_ids: list[int] = Field(default_factory=list, max_length=10)
    preferred_skill_ids: list[int] = Field(default_factory=list, max_length=10)


class JobListingOut(BaseModel):
    """Issue #22: one row of an employer's "pick a job" list."""
    model_config = {"from_attributes": True}

    id: int
    title: str
    company_name: str
    location_city: str
    posted_at: str


class JobDetailsOut(BaseModel):
    """Issue #22: full job detail for the edit form -- same shape as
    JobCreate's editable fields, plus the server-assigned id.
    """
    id: int
    title: str
    company_name: str
    description: str
    location_city: str
    location_region: str
    salary_min: int | None
    salary_max: int | None
    primary_occupation_id: int | None
    experience_level: str | None
    required_skill_ids: list[int]
    preferred_skill_ids: list[int]


class JobDetailsUpdate(BaseModel):
    title: str
    company_name: str
    description: str
    location_city: str
    location_region: str
    salary_min: int | None = None
    salary_max: int | None = None
    primary_occupation_id: int | None = None
    experience_level: str | None = None
    required_skill_ids: list[int] = Field(default_factory=list, max_length=10)
    preferred_skill_ids: list[int] = Field(default_factory=list, max_length=10)
