"""Pure-logic tests for app/golden/inject_noise.py -- no DB."""
import random

from app.golden.inject_noise import _pick_unrelated_skill


class _FakeSkill:
    def __init__(self, id_):
        self.id = id_


def test_pick_unrelated_skill_never_returns_an_excluded_id():
    # Real bug found live: a candidate who already happened to hold one of
    # the "unrelated domain" skills (via the small amount of normal
    # cross-domain noise every profile has) collided with
    # candidate_skills' unique (candidate_id, skill_id) constraint.
    skills_by_domain = {
        "technology": [_FakeSkill(1), _FakeSkill(2)],
        "healthcare": [_FakeSkill(3)],
        "sales_marketing": [_FakeSkill(4), _FakeSkill(5)],
    }
    rng = random.Random(1)
    exclude_ids = {4}  # candidate already holds skill 4 (sales_marketing)

    for _ in range(20):
        skill = _pick_unrelated_skill(rng, skills_by_domain, "technology", exclude_ids)
        assert skill is not None
        assert skill.id not in exclude_ids
        assert skill.id in {3, 5}  # never technology (own domain), never excluded


def test_pick_unrelated_skill_never_from_own_domain():
    skills_by_domain = {
        "technology": [_FakeSkill(1)],
        "healthcare": [_FakeSkill(2)],
    }
    rng = random.Random(2)

    for _ in range(20):
        skill = _pick_unrelated_skill(rng, skills_by_domain, "technology", set())
        assert skill.id == 2  # only healthcare is "unrelated" to technology here


def test_pick_unrelated_skill_returns_none_when_every_other_domain_excluded():
    skills_by_domain = {
        "technology": [_FakeSkill(1)],
        "healthcare": [_FakeSkill(2)],
    }
    rng = random.Random(3)

    assert _pick_unrelated_skill(rng, skills_by_domain, "technology", {2}) is None
