"""Deterministic synthetic data generator for JME.

Run with: python -m app.ingestion.generate_synthetic_data

This module simulates the boundary of an upstream data-engineering-owned
pipeline: in a real deployment, candidates/jobs/skills source data would be
ingested from an external system of record, not generated here. It is not
shipped in the API's production image (see docker/Dockerfile.seed).

Scale (documented assumption, see docs/assumptions-register.md A4):
  candidates=2000, jobs=1000, skills=300, occupations=150, courses=100

Deliberate imperfections introduced (per PRD/brief, so matching is non-trivial):
  - skill synonyms (messy free text variants of canonical skill names)
  - missing candidate fields (salary expectations, occupation)
  - duplicate-looking job postings (same company+title, slightly different description)
  - some candidates/jobs in locations with no exact matches
"""
import random
from datetime import datetime, timedelta, timezone

from faker import Faker
from sqlalchemy import text

from app.db import Base, SessionLocal, engine
from app.ingestion.source_models import (
    AssessmentResult,
    Candidate,
    CandidateSkill,
    CandidateSkillAssessment,
    Course,
    CourseSkill,
    EmploymentType,
    EvidenceSource,
    Job,
    JobSkill,
    Occupation,
    OccupationSkill,
    ProficiencyLevel,
    RequirementLevel,
    Skill,
    SkillSynonym,
)

SEED = 42
N_CANDIDATES = 2000
N_JOBS = 1000
N_OCCUPATIONS = 150
N_COURSES = 100

SKILL_TAXONOMY = [
    ("Python", "programming"), ("SQL", "data"), ("JavaScript", "programming"),
    ("TypeScript", "programming"), ("React", "frontend"), ("FastAPI", "backend"),
    ("Django", "backend"), ("Docker", "platform"), ("Kubernetes", "platform"),
    ("AWS", "cloud"), ("Azure", "cloud"), ("GCP", "cloud"), ("Machine Learning", "ai"),
    ("Deep Learning", "ai"), ("NLP", "ai"), ("Data Analysis", "data"),
    ("Project Management", "business"), ("Communication", "soft_skill"),
    ("Leadership", "soft_skill"), ("Sales", "business"), ("Marketing", "business"),
    ("Customer Service", "business"), ("Accounting", "finance"), ("Excel", "office"),
    ("PowerPoint", "office"), ("Negotiation", "soft_skill"), ("Recruiting", "hr"),
    ("Payroll", "hr"), ("Nursing", "healthcare"), ("Patient Care", "healthcare"),
    ("Electrical Wiring", "trades"), ("Plumbing", "trades"), ("Welding", "trades"),
    ("Carpentry", "trades"), ("Forklift Operation", "logistics"),
    ("Warehouse Management", "logistics"), ("Supply Chain", "logistics"),
    ("Graphic Design", "creative"), ("Video Editing", "creative"),
    ("Copywriting", "creative"), ("SEO", "marketing"), ("Social Media", "marketing"),
    ("Java", "programming"), ("C#", "programming"), ("Go", "programming"),
    ("Node.js", "backend"), ("PostgreSQL", "data"), ("MongoDB", "data"),
    ("Tableau", "data"), ("Power BI", "data"), ("Linux", "platform"),
    ("Networking", "it"), ("Cybersecurity", "it"), ("Teaching", "education"),
    ("Curriculum Design", "education"), ("Public Speaking", "soft_skill"),
    ("Data Entry", "office"), ("Bookkeeping", "finance"), ("Budgeting", "finance"),
    ("Quality Assurance", "engineering"), ("Manufacturing", "engineering"),
    ("CAD", "engineering"), ("Agile", "business"), ("Scrum", "business"),
]

SYNONYM_MAP = {
    "Python": ["py", "python3", "python programming"],
    "JavaScript": ["js", "javascript (es6)", "vanilla js"],
    "Machine Learning": ["ml", "machine-learning"],
    "Project Management": ["pm", "proj. management"],
    "Customer Service": ["customer svc", "client service"],
    "PostgreSQL": ["postgres", "psql"],
    "Communication": ["communication skills"],
}

OCCUPATION_TITLES = [
    "Software Engineer", "Data Analyst", "Data Scientist", "Project Manager",
    "Sales Representative", "Marketing Specialist", "Customer Service Agent",
    "Accountant", "HR Coordinator", "Registered Nurse", "Electrician", "Plumber",
    "Warehouse Associate", "Supply Chain Analyst", "Graphic Designer",
    "Content Writer", "Network Administrator", "Security Analyst", "Teacher",
    "Welder", "Carpenter", "Forklift Operator", "QA Engineer",
    "Manufacturing Technician", "Mechanical Engineer", "Scrum Master",
]

COMPANY_NAMES_EXTRA = ["Acme", "Globex", "Initech", "Umbrella", "Stark", "Wayne"]


def truncate_all():
    """Clear existing rows without touching DDL. Schema creation/changes are
    owned exclusively by Alembic migrations (docker/Dockerfile.migrate), never
    by this ingestion script — keeps the DDL/DML responsibility split clean."""
    table_names = [t.name for t in reversed(Base.metadata.sorted_tables)]
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE"))


def generate():
    fake = Faker()
    Faker.seed(SEED)
    rng = random.Random(SEED)

    session = SessionLocal()

    # --- Skills + synonyms ---------------------------------------------------
    skills = []
    for name, category in SKILL_TAXONOMY:
        s = Skill(name=name, category=category)
        session.add(s)
        skills.append(s)
    session.flush()

    for name, variants in SYNONYM_MAP.items():
        skill = next(s for s in skills if s.name == name)
        for variant in variants:
            session.add(SkillSynonym(skill_id=skill.id, synonym_text=variant))

    # --- Occupations + expected skill profile --------------------------------
    occupations = []
    for i in range(N_OCCUPATIONS):
        title = OCCUPATION_TITLES[i % len(OCCUPATION_TITLES)]
        suffix = "" if i < len(OCCUPATION_TITLES) else f" {i // len(OCCUPATION_TITLES) + 1}"
        occ = Occupation(code=f"OCC-{i:04d}", title=f"{title}{suffix}")
        session.add(occ)
        occupations.append(occ)
    session.flush()

    for occ in occupations:
        profile_skills = rng.sample(skills, k=rng.randint(4, 8))
        for sk in profile_skills:
            session.add(
                OccupationSkill(
                    occupation_id=occ.id,
                    skill_id=sk.id,
                    importance=round(rng.uniform(0.5, 1.0), 2),
                )
            )

    # --- Courses --------------------------------------------------------------
    levels = ["intro", "intermediate", "advanced"]
    for i in range(N_COURSES):
        anchor_skill = skills[i % len(skills)]
        course_suffix = rng.choice(["Fundamentals", "Masterclass", "Bootcamp", "Certification"])
        course = Course(
            title=f"{anchor_skill.name} {course_suffix}",
            provider=rng.choice(["Coursera", "Udemy", "LinkedIn Learning", "Local College", "edX"]),
            level=rng.choice(levels),
            duration_hours=rng.choice([4, 8, 20, 40, 80]),
        )
        session.add(course)
        session.flush()
        session.add(CourseSkill(course_id=course.id, skill_id=anchor_skill.id, coverage_weight=1.0))
        for extra in rng.sample(skills, k=rng.randint(0, 2)):
            if extra.id != anchor_skill.id:
                coverage_weight = round(rng.uniform(0.2, 0.6), 2)
                session.add(
                    CourseSkill(course_id=course.id, skill_id=extra.id, coverage_weight=coverage_weight)
                )

    session.flush()

    # --- Candidates -------------------------------------------------------------
    for i in range(N_CANDIDATES):
        occ = rng.choice(occupations) if rng.random() > 0.05 else None  # 5% missing occupation
        has_salary = rng.random() > 0.15  # 15% missing salary expectation
        candidate = Candidate(
            full_name=fake.name(),
            email=f"candidate{i}@example-synthetic.test",
            location_city=fake.city(),
            location_region=fake.state(),
            years_experience=rng.randint(0, 25),
            desired_salary_min=rng.randint(30, 90) * 1000 if has_salary else None,
            desired_salary_max=rng.randint(90, 160) * 1000 if has_salary else None,
            primary_occupation_id=occ.id if occ else None,
            created_at=datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
        )
        session.add(candidate)
        session.flush()

        base_pool = skills
        if occ:
            occ_skill_ids = {os_.skill_id for os_ in occ.skills}
            weighted_pool = [s for s in skills if s.id in occ_skill_ids] or skills
            base_pool = weighted_pool + rng.sample(skills, k=min(5, len(skills)))

        n_skills = rng.randint(2, 10)
        chosen = rng.sample(base_pool, k=min(n_skills, len(base_pool)))
        seen_skill_ids = set()
        for sk in chosen:
            if sk.id in seen_skill_ids:
                continue
            seen_skill_ids.add(sk.id)
            session.add(
                CandidateSkill(
                    candidate_id=candidate.id,
                    skill_id=sk.id,
                    proficiency=rng.choice(list(ProficiencyLevel)),
                    evidence_source=rng.choice(list(EvidenceSource)),
                )
            )

        # Explicit assessments for ~20% of candidates, on skills not already claimed
        if rng.random() < 0.2:
            unclaimed = [s for s in skills if s.id not in seen_skill_ids]
            if unclaimed:
                assessed_skill = rng.choice(unclaimed)
                session.add(
                    CandidateSkillAssessment(
                        candidate_id=candidate.id,
                        skill_id=assessed_skill.id,
                        result=rng.choice(list(AssessmentResult)),
                        assessed_at=datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 180)),
                    )
                )

    session.flush()

    # --- Jobs -------------------------------------------------------------------
    for i in range(N_JOBS):
        occ = rng.choice(occupations)
        company = f"{rng.choice(COMPANY_NAMES_EXTRA)} {fake.company_suffix()}"
        has_salary = rng.random() > 0.1
        job = Job(
            title=occ.title,
            company_name=company,
            occupation_id=occ.id,
            description_raw=(
                f"We are seeking a {occ.title} to join {company}. "
                f"{fake.paragraph(nb_sentences=3)}"
            ),
            location_city=fake.city(),
            location_region=fake.state(),
            salary_min=rng.randint(30, 90) * 1000 if has_salary else None,
            salary_max=rng.randint(90, 160) * 1000 if has_salary else None,
            employment_type=rng.choice(list(EmploymentType)),
            posted_at=datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 90)),
        )
        session.add(job)
        session.flush()

        occ_skill_ids = {os_.skill_id for os_ in occ.skills}
        req_pool = [s for s in skills if s.id in occ_skill_ids] or rng.sample(skills, k=5)
        for sk in req_pool:
            session.add(
                JobSkill(
                    job_id=job.id,
                    skill_id=sk.id,
                    requirement_level=rng.choices(
                        list(RequirementLevel), weights=[0.7, 0.3]
                    )[0],
                )
            )

    session.commit()
    session.close()


if __name__ == "__main__":
    truncate_all()
    generate()
    print("Synthetic data generated.")
