"""Pure-logic tests for the Phase 20 domain-anchored skill sampling — no DB
involved. See docs/data-at-scale.md.
"""
import random

from app.ingestion.generate_synthetic_data import (
    SKILL_DOMAINS,
    SKILL_TAXONOMY,
    _assign_domain_anchored_skills,
)


class _FakeSkill:
    def __init__(self, id_, category):
        self.id = id_
        self.category = category


def test_every_taxonomy_category_is_covered_by_exactly_one_domain():
    categories = {category for _name, category in SKILL_TAXONOMY}
    domain_categories = [cat for categories_set in SKILL_DOMAINS.values() for cat in categories_set]

    assert categories == set(domain_categories), "every category must be covered by some domain"
    assert len(domain_categories) == len(set(domain_categories)), "no category in two domains"


def test_assign_domain_anchored_skills_returns_k_skills():
    skills = [_FakeSkill(i, cat) for i, (_name, cat) in enumerate(SKILL_TAXONOMY)]
    skills_by_domain = {
        domain: [s for s in skills if s.category in categories]
        for domain, categories in SKILL_DOMAINS.items()
    }
    domain_names = list(SKILL_DOMAINS.keys())
    rng = random.Random(42)

    result = _assign_domain_anchored_skills(rng, skills, skills_by_domain, domain_names, k=6)

    assert len(result) == 6
    assert len(result) == len({s.id for s in result}), "no duplicate skills"


def test_assign_domain_anchored_skills_majority_from_one_domain():
    skills = [_FakeSkill(i, cat) for i, (_name, cat) in enumerate(SKILL_TAXONOMY)]
    skills_by_domain = {
        domain: [s for s in skills if s.category in categories]
        for domain, categories in SKILL_DOMAINS.items()
    }
    domain_names = list(SKILL_DOMAINS.keys())
    rng = random.Random(7)

    result = _assign_domain_anchored_skills(rng, skills, skills_by_domain, domain_names, k=8)

    domain_by_category = {
        cat: domain for domain, categories in SKILL_DOMAINS.items() for cat in categories
    }
    domain_counts: dict[str, int] = {}
    for s in result:
        domain = domain_by_category[s.category]
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    # At least 70% (per the documented 70-80% anchor ratio) came from a single domain.
    assert max(domain_counts.values()) / len(result) >= 0.7
