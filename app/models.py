import enum
from datetime import datetime

from sqlalchemy import (
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProficiencyLevel(str, enum.Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"
    expert = "expert"


class EvidenceSource(str, enum.Enum):
    self_reported = "self_reported"
    assessed = "assessed"
    inferred = "inferred"


class AssessmentResult(str, enum.Enum):
    present = "present"
    absent = "absent"


class RequirementLevel(str, enum.Enum):
    required = "required"
    preferred = "preferred"


class EmploymentType(str, enum.Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"


# --- Canonical taxonomy -----------------------------------------------------

class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    synonyms: Mapped[list["SkillSynonym"]] = relationship(back_populates="skill")


class SkillSynonym(Base):
    """Raw/variant text that normalizes to a canonical skill.

    Separate from Skill so the dictionary-based extractor (Use Case C) can map
    messy free-text ("py", "pytorch ") to one canonical skill without polluting
    the taxonomy itself.
    """

    __tablename__ = "skill_synonyms"
    __table_args__ = (UniqueConstraint("synonym_text", name="uq_synonym_text"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    synonym_text: Mapped[str] = mapped_column(String(150))

    skill: Mapped["Skill"] = relationship(back_populates="synonyms")


class Occupation(Base):
    __tablename__ = "occupations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    title: Mapped[str] = mapped_column(String(150))

    skills: Mapped[list["OccupationSkill"]] = relationship(back_populates="occupation")


class OccupationSkill(Base):
    """Expected skill profile for an occupation, used for occupation-level
    similarity and as a fallback when a job posting under-specifies requirements."""

    __tablename__ = "occupation_skills"
    __table_args__ = (UniqueConstraint("occupation_id", "skill_id", name="uq_occ_skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    occupation_id: Mapped[int] = mapped_column(ForeignKey("occupations.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    importance: Mapped[float] = mapped_column(Numeric(3, 2), default=1.0)

    occupation: Mapped["Occupation"] = relationship(back_populates="skills")
    skill: Mapped["Skill"] = relationship()


# --- Courses -----------------------------------------------------------------

class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(100))
    level: Mapped[str] = mapped_column(String(30))  # intro/intermediate/advanced
    duration_hours: Mapped[int] = mapped_column()

    skills: Mapped[list["CourseSkill"]] = relationship(back_populates="course")


class CourseSkill(Base):
    """Skills a course covers, with a coverage weight (how central the skill is
    to the course) used for skill-gap coverage scoring (Use Case E)."""

    __tablename__ = "course_skills"
    __table_args__ = (UniqueConstraint("course_id", "skill_id", name="uq_course_skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    coverage_weight: Mapped[float] = mapped_column(Numeric(3, 2), default=1.0)

    course: Mapped["Course"] = relationship(back_populates="skills")
    skill: Mapped["Skill"] = relationship()


# --- Candidates ----------------------------------------------------------------

class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    location_city: Mapped[str] = mapped_column(String(100))
    location_region: Mapped[str] = mapped_column(String(100))
    years_experience: Mapped[int] = mapped_column()
    desired_salary_min: Mapped[int | None] = mapped_column(nullable=True)
    desired_salary_max: Mapped[int | None] = mapped_column(nullable=True)
    primary_occupation_id: Mapped[int | None] = mapped_column(
        ForeignKey("occupations.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column()

    skills: Mapped[list["CandidateSkill"]] = relationship(back_populates="candidate")
    assessments: Mapped[list["CandidateSkillAssessment"]] = relationship(
        back_populates="candidate"
    )


class CandidateSkill(Base):
    """Positive skill evidence only. Absence of a row means 'not recorded' —
    NOT 'does not have'. See CandidateSkillAssessment for explicit negative
    evidence (Use Case D requirement)."""

    __tablename__ = "candidate_skills"
    __table_args__ = (UniqueConstraint("candidate_id", "skill_id", name="uq_candidate_skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    proficiency: Mapped[ProficiencyLevel] = mapped_column(Enum(ProficiencyLevel))
    evidence_source: Mapped[EvidenceSource] = mapped_column(Enum(EvidenceSource))

    candidate: Mapped["Candidate"] = relationship(back_populates="skills")
    skill: Mapped["Skill"] = relationship()


class CandidateSkillAssessment(Base):
    """Explicit assessment outcome for a candidate/skill pair, distinct from
    CandidateSkill. A row here with result=absent means the skill was actively
    checked and the candidate did not demonstrate it — different from no row
    at all (never assessed)."""

    __tablename__ = "candidate_skill_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    result: Mapped[AssessmentResult] = mapped_column(Enum(AssessmentResult))
    assessed_at: Mapped[datetime] = mapped_column()

    candidate: Mapped["Candidate"] = relationship(back_populates="assessments")
    skill: Mapped["Skill"] = relationship()


# --- Jobs ----------------------------------------------------------------------

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    company_name: Mapped[str] = mapped_column(String(150))
    occupation_id: Mapped[int | None] = mapped_column(
        ForeignKey("occupations.id"), nullable=True
    )
    description_raw: Mapped[str] = mapped_column(Text)
    location_city: Mapped[str] = mapped_column(String(100))
    location_region: Mapped[str] = mapped_column(String(100))
    salary_min: Mapped[int | None] = mapped_column(nullable=True)
    salary_max: Mapped[int | None] = mapped_column(nullable=True)
    employment_type: Mapped[EmploymentType] = mapped_column(Enum(EmploymentType))
    posted_at: Mapped[datetime] = mapped_column()

    requirements: Mapped[list["JobSkill"]] = relationship(back_populates="job")


class JobSkill(Base):
    __tablename__ = "job_skills"
    __table_args__ = (UniqueConstraint("job_id", "skill_id", name="uq_job_skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    requirement_level: Mapped[RequirementLevel] = mapped_column(Enum(RequirementLevel))

    job: Mapped["Job"] = relationship(back_populates="requirements")
    skill: Mapped["Skill"] = relationship()
