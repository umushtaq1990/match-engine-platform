"""Deterministic synthetic data generator for JME.

Run with: python -m app.ingestion.generate_synthetic_data [--scale small|large]

This module simulates the boundary of an upstream data-engineering-owned
pipeline: in a real deployment, candidates/jobs/skills source data would be
ingested from an external system of record, not generated here. It is not
shipped in the API's production image (see docker/Dockerfile.seed).

Scale: "small" (default, unchanged since Phase 2 — see
docs/assumptions-register.md A4) is candidates=2000, jobs=1000,
occupations=150, courses=100 — every existing test/CI check/documented
baseline is measured against this. "large" (Phase 20,
docs/data-at-scale.md) is candidates=1,000,000, jobs=50,000,
occupations=3,000, courses=2,000 — a real stress-test invocation, never the
default anyone runs by accident.

Deliberate imperfections introduced (per PRD/brief, so matching is non-trivial):
  - skill synonyms (messy free text variants of canonical skill names)
  - missing candidate fields (salary expectations, occupation)
  - duplicate-looking job postings (same company+title, slightly different description)
  - some candidates/jobs in locations with no exact matches

Phase 20: occupation skill profiles are anchored to one "domain" (a coarse
grouping of the existing fine-grained skill categories — see SKILL_DOMAINS)
rather than sampled uniformly across all 64 skills. This is what gives the
knowledge graph (docs/knowledge-graph.md) real co-occurrence structure to
find — Phase 19 root-caused the previous uniform-random sampling as the
reason the skill co-occurrence graph was complete (every pair connected,
no community structure possible). Candidate/job skill sampling already
derives its pool from the occupation's profile, so this one change
cascades without touching that code.
"""
import argparse
import random
from datetime import datetime, timedelta, timezone

from faker import Faker
from sqlalchemy import insert, select, text

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

SCALE_PRESETS = {
    "small": {"n_candidates": 2000, "n_jobs": 1000, "n_occupations": 150, "n_courses": 100},
    "large": {"n_candidates": 1_000_000, "n_jobs": 50_000, "n_occupations": 3000, "n_courses": 2000},
}

# Rows per bulk-insert statement for the tables that scale to "large" —
# candidates/jobs and their skill junction tables. Keeps memory bounded
# regardless of total row count; see docs/data-at-scale.md.
CHUNK_SIZE = 10_000

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

# Phase 20 (docs/data-at-scale.md): coarse grouping of the fine-grained
# categories above, used ONLY to anchor occupation skill-profile sampling —
# never written to the database, so comparing discovered graph clusters
# against the real `category` column stays a legitimate, independent check.
SKILL_DOMAINS = {
    "software_engineering": {
        "programming", "frontend", "backend", "platform", "cloud", "ai", "data", "it",
    },
    "business_ops": {"business", "soft_skill", "hr", "office", "finance"},
    "trades_logistics": {"trades", "logistics", "engineering"},
    "people_services": {"healthcare", "education", "creative", "marketing"},
}

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

# Phase 16: job descriptions weave in their own required/preferred skills (by
# canonical name or synonym), rather than unrelated filler text, so Use Case C
# (free-text skill extraction, docs/skill-extraction.md) has real ground
# truth to extract against — the existing JobSkill rows for that job.
REQUIRED_TEMPLATES = [
    "The ideal candidate must have strong experience with {skills}.",
    "You will need hands-on expertise in {skills}.",
    "Required skills: {skills}.",
]
PREFERRED_TEMPLATES = [
    "Experience with {skills} would be a plus.",
    "Familiarity with {skills} is preferred but not required.",
    "Bonus points for knowledge of {skills}.",
]


def _mention_phrase(rng, skill):
    """Sometimes the canonical skill name, sometimes a known synonym — so
    extraction can't just string-match the taxonomy table verbatim."""
    variants = SYNONYM_MAP.get(skill.name)
    if variants and rng.random() < 0.4:
        return rng.choice(variants)
    return skill.name


def _join_skill_phrases(phrases):
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return ", ".join(phrases[:-1]) + f", and {phrases[-1]}"


def _build_job_description(rng, fake, title, company, required_phrases, preferred_phrases):
    parts = [f"We are seeking a {title} to join {company}. {fake.paragraph(nb_sentences=2)}"]
    if required_phrases:
        parts.append(
            rng.choice(REQUIRED_TEMPLATES).format(skills=_join_skill_phrases(required_phrases))
        )
    if preferred_phrases:
        parts.append(
            rng.choice(PREFERRED_TEMPLATES).format(skills=_join_skill_phrases(preferred_phrases))
        )
    return " ".join(parts)


def truncate_all():
    """Clear existing rows without touching DDL. Schema creation/changes are
    owned exclusively by Alembic migrations (docker/Dockerfile.migrate), never
    by this ingestion script — keeps the DDL/DML responsibility split clean."""
    table_names = [t.name for t in reversed(Base.metadata.sorted_tables)]
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE"))


def _reset_sequence(session, table_name: str, id_column: str = "id") -> None:
    """Bulk inserts below assign primary keys explicitly (so skill-junction
    rows can reference a candidate/job id without a round-trip) — this
    re-syncs the table's identity sequence afterward so anything inserted
    later via normal ORM flow still gets a non-colliding id."""
    session.execute(text(
        f"SELECT setval(pg_get_serial_sequence('{table_name}', '{id_column}'), "
        f"COALESCE((SELECT MAX({id_column}) FROM {table_name}), 1))"
    ))


def _assign_domain_anchored_skills(rng, skills, skills_by_domain, domain_names, k):
    """~70-80% of a k-sized skill profile from one randomly chosen domain's
    pool, the rest uniform-random noise — see SKILL_DOMAINS and
    docs/data-at-scale.md."""
    anchor_domain = rng.choice(domain_names)
    domain_pool = skills_by_domain[anchor_domain]
    n_anchor = min(max(1, round(k * rng.uniform(0.7, 0.8))), len(domain_pool), k)
    anchor_skills = rng.sample(domain_pool, k=n_anchor)
    anchor_ids = {s.id for s in anchor_skills}
    remaining_pool = [s for s in skills if s.id not in anchor_ids]
    n_noise = min(k - n_anchor, len(remaining_pool))
    noise_skills = rng.sample(remaining_pool, k=n_noise) if n_noise else []
    return anchor_skills + noise_skills


def _generate_candidates(session, rng, fake, occupations, skills, occ_skill_ids, n_candidates):
    candidate_rows, skill_rows, assessment_rows = [], [], []

    def _flush():
        if candidate_rows:
            session.execute(insert(Candidate.__table__), candidate_rows)
        if skill_rows:
            session.execute(insert(CandidateSkill.__table__), skill_rows)
        if assessment_rows:
            session.execute(insert(CandidateSkillAssessment.__table__), assessment_rows)
        session.flush()
        candidate_rows.clear()
        skill_rows.clear()
        assessment_rows.clear()

    for i in range(n_candidates):
        candidate_id = i + 1
        occ = rng.choice(occupations) if rng.random() > 0.05 else None  # 5% missing occupation
        has_salary = rng.random() > 0.15  # 15% missing salary expectation
        candidate_rows.append({
            "id": candidate_id,
            "full_name": fake.name(),
            "email": f"candidate{i}@example-synthetic.test",
            "location_city": fake.city(),
            "location_region": fake.state(),
            "years_experience": rng.randint(0, 25),
            "desired_salary_min": rng.randint(30, 90) * 1000 if has_salary else None,
            "desired_salary_max": rng.randint(90, 160) * 1000 if has_salary else None,
            "primary_occupation_id": occ.id if occ else None,
            "created_at": datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
        })

        base_pool = skills
        if occ:
            occ_ids = occ_skill_ids.get(occ.id, set())
            weighted_pool = [s for s in skills if s.id in occ_ids] or skills
            base_pool = weighted_pool + rng.sample(skills, k=min(5, len(skills)))

        n_skills = rng.randint(2, 10)
        chosen = rng.sample(base_pool, k=min(n_skills, len(base_pool)))
        seen_skill_ids = set()
        for sk in chosen:
            if sk.id in seen_skill_ids:
                continue
            seen_skill_ids.add(sk.id)
            skill_rows.append({
                "candidate_id": candidate_id,
                "skill_id": sk.id,
                "proficiency": rng.choice(list(ProficiencyLevel)),
                "evidence_source": rng.choice(list(EvidenceSource)),
            })

        # Explicit assessments for ~20% of candidates, on skills not already claimed
        if rng.random() < 0.2:
            unclaimed = [s for s in skills if s.id not in seen_skill_ids]
            if unclaimed:
                assessed_skill = rng.choice(unclaimed)
                assessment_rows.append({
                    "candidate_id": candidate_id,
                    "skill_id": assessed_skill.id,
                    "result": rng.choice(list(AssessmentResult)),
                    "assessed_at": datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 180)),
                })

        if len(candidate_rows) >= CHUNK_SIZE:
            _flush()

    _flush()
    _reset_sequence(session, "candidates")


def _generate_jobs(session, rng, fake, occupations, skills, occ_skill_ids, n_jobs):
    job_rows, skill_rows = [], []

    def _flush():
        if job_rows:
            session.execute(insert(Job.__table__), job_rows)
        if skill_rows:
            session.execute(insert(JobSkill.__table__), skill_rows)
        session.flush()
        job_rows.clear()
        skill_rows.clear()

    for i in range(n_jobs):
        job_id = i + 1
        occ = rng.choice(occupations)
        company = f"{rng.choice(COMPANY_NAMES_EXTRA)} {fake.company_suffix()}"
        has_salary = rng.random() > 0.1

        occ_ids = occ_skill_ids.get(occ.id, set())
        req_pool = [s for s in skills if s.id in occ_ids] or rng.sample(skills, k=5)
        skill_levels = [
            (sk, rng.choices(list(RequirementLevel), weights=[0.7, 0.3])[0])
            for sk in req_pool
        ]
        required_phrases = [
            _mention_phrase(rng, sk) for sk, lvl in skill_levels if lvl == RequirementLevel.required
        ]
        preferred_phrases = [
            _mention_phrase(rng, sk) for sk, lvl in skill_levels if lvl == RequirementLevel.preferred
        ]

        job_rows.append({
            "id": job_id,
            "title": occ.title,
            "company_name": company,
            "occupation_id": occ.id,
            "description_raw": _build_job_description(
                rng, fake, occ.title, company, required_phrases, preferred_phrases
            ),
            "location_city": fake.city(),
            "location_region": fake.state(),
            "salary_min": rng.randint(30, 90) * 1000 if has_salary else None,
            "salary_max": rng.randint(90, 160) * 1000 if has_salary else None,
            "employment_type": rng.choice(list(EmploymentType)),
            "posted_at": datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 90)),
        })
        for sk, level in skill_levels:
            skill_rows.append({"job_id": job_id, "skill_id": sk.id, "requirement_level": level})

        if len(job_rows) >= CHUNK_SIZE:
            _flush()

    _flush()
    _reset_sequence(session, "jobs")


def generate(
    n_candidates: int = SCALE_PRESETS["small"]["n_candidates"],
    n_jobs: int = SCALE_PRESETS["small"]["n_jobs"],
    n_occupations: int = SCALE_PRESETS["small"]["n_occupations"],
    n_courses: int = SCALE_PRESETS["small"]["n_courses"],
    seed: int = SEED,
):
    fake = Faker()
    Faker.seed(seed)
    rng = random.Random(seed)

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

    skills_by_domain = {
        domain: [s for s in skills if s.category in categories]
        for domain, categories in SKILL_DOMAINS.items()
    }
    domain_names = list(SKILL_DOMAINS.keys())

    # --- Occupations + expected skill profile --------------------------------
    occupations = []
    for i in range(n_occupations):
        title = OCCUPATION_TITLES[i % len(OCCUPATION_TITLES)]
        suffix = "" if i < len(OCCUPATION_TITLES) else f" {i // len(OCCUPATION_TITLES) + 1}"
        occ = Occupation(code=f"OCC-{i:04d}", title=f"{title}{suffix}")
        session.add(occ)
        occupations.append(occ)
    session.flush()

    for occ in occupations:
        k = rng.randint(4, 8)
        profile_skills = _assign_domain_anchored_skills(rng, skills, skills_by_domain, domain_names, k)
        for sk in profile_skills:
            session.add(
                OccupationSkill(
                    occupation_id=occ.id,
                    skill_id=sk.id,
                    importance=round(rng.uniform(0.5, 1.0), 2),
                )
            )
    session.flush()

    # Precomputed once, not re-queried per candidate/job (Phase 9 N+1 discipline,
    # now load-bearing at Phase 20 scale too) — occupation_id -> {skill_id, ...}
    occ_skill_ids: dict[int, set[int]] = {}
    for occupation_id, skill_id in session.execute(
        select(OccupationSkill.occupation_id, OccupationSkill.skill_id)
    ).all():
        occ_skill_ids.setdefault(occupation_id, set()).add(skill_id)

    # --- Courses --------------------------------------------------------------
    levels = ["intro", "intermediate", "advanced"]
    for i in range(n_courses):
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

    # --- Candidates, Jobs (bulk-inserted, chunked — see docs/data-at-scale.md) --
    _generate_candidates(session, rng, fake, occupations, skills, occ_skill_ids, n_candidates)
    _generate_jobs(session, rng, fake, occupations, skills, occ_skill_ids, n_jobs)

    session.commit()
    session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=sorted(SCALE_PRESETS.keys()), default="small")
    args = parser.parse_args()

    preset = SCALE_PRESETS[args.scale]
    truncate_all()
    generate(**preset)
    print(f"Synthetic data generated (scale={args.scale}): {preset}")
