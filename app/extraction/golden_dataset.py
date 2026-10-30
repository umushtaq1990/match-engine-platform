"""Loads both parts of the Use Case C golden dataset (docs/skill-extraction.md):

- Part A, generator-injected: derived live from Job/JobSkill rows whose
  description text was woven from those exact skills
  (app/ingestion/generate_synthetic_data.py) — cheap, deterministic, scales
  for free. Tests parsing/canonicalization correctness, not real-world
  recall (the text is constructed from the ground truth by design).
- Part B, hand-authored: data/golden/skill_extraction.yaml — harder,
  realistic cases a template generator can't produce (synonym-only mentions,
  ambiguous requirement language, out-of-taxonomy skills).
"""
import random
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.source_models import Job, JobSkill, Skill

GOLDEN_FILE = Path(__file__).resolve().parents[2] / "data" / "golden" / "skill_extraction.yaml"


def load_generator_injected(session: Session, sample_size: int = 30, seed: int = 42) -> list[dict]:
    job_ids = session.execute(select(Job.id).order_by(Job.id)).scalars().all()
    rng = random.Random(seed)
    sampled = sorted(rng.sample(job_ids, k=min(sample_size, len(job_ids))))

    examples = []
    for job_id in sampled:
        job = session.get(Job, job_id)
        job_skills = session.execute(
            select(JobSkill).where(JobSkill.job_id == job_id)
        ).scalars().all()
        examples.append({
            "example_id": f"job:{job_id}",
            "source": "generator",
            "text": job.description_raw,
            "expected": [
                {"skill_id": js.skill_id, "requirement_level": js.requirement_level.value}
                for js in job_skills
            ],
        })
    return examples


def load_hand_authored(session: Session) -> list[dict]:
    if not GOLDEN_FILE.exists():
        return []
    with open(GOLDEN_FILE, encoding="utf-8") as f:
        raw_examples = yaml.safe_load(f) or []

    name_to_id = {s.name: s.id for s in session.query(Skill).all()}

    examples = []
    for item in raw_examples:
        expected = []
        for exp in item.get("expected", []):
            skill_id = name_to_id.get(exp["skill_name"])
            if skill_id is None:
                # Deliberately out-of-taxonomy in some hand-authored cases —
                # correct pipeline behavior is to not match it at all.
                continue
            expected.append({"skill_id": skill_id, "requirement_level": exp["requirement_level"]})
        examples.append({
            "example_id": f"golden:{item['id']}",
            "source": "hand_authored",
            "text": item["text"],
            "expected": expected,
        })
    return examples
