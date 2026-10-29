from app.matching.eligibility import check_eligibility
from tests.matching.test_scoring import make_candidate, make_job


def test_no_required_skills_is_always_eligible():
    candidate = make_candidate(skill_ids=set())
    job = make_job(required_skill_ids=set())
    assert check_eligibility(candidate, job).eligible is True


def test_zero_overlap_with_required_skills_is_ineligible():
    candidate = make_candidate(skill_ids={99})
    job = make_job(required_skill_ids={1, 2})
    result = check_eligibility(candidate, job)
    assert result.eligible is False
    assert result.reason is not None


def test_partial_overlap_with_required_skills_is_eligible():
    candidate = make_candidate(skill_ids={1})
    job = make_job(required_skill_ids={1, 2})
    assert check_eligibility(candidate, job).eligible is True
