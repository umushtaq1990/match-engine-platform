from app.matching.profiles import CandidateProfile, JobProfile
from app.matching.scoring import UNKNOWN_DATA_SCORE, score_candidate_job


def make_candidate(**overrides) -> CandidateProfile:
    defaults = dict(
        id=1, full_name="Test Candidate", skill_ids=set(), confirmed_absent_skill_ids=set(),
        location_city="Metropolis", location_region="StateA", desired_salary_min=50000,
        desired_salary_max=80000, primary_occupation_id=1, years_experience=5,
    )
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def make_job(**overrides) -> JobProfile:
    defaults = dict(
        id=1, title="Test Job", company_name="Acme", required_skill_ids=set(),
        preferred_skill_ids=set(), location_city="Metropolis", location_region="StateA",
        salary_min=60000, salary_max=90000, occupation_id=1,
    )
    defaults.update(overrides)
    return JobProfile(**defaults)


def test_full_required_skill_match_scores_perfectly_on_skill_component():
    candidate = make_candidate(skill_ids={1, 2, 3})
    job = make_job(required_skill_ids={1, 2, 3})
    result = score_candidate_job(candidate, job)
    assert result.skill == 1.0
    assert result.matching_required_skill_ids == {1, 2, 3}
    assert result.missing_required_skill_ids == set()


def test_zero_required_skill_overlap_scores_zero_on_required_portion():
    candidate = make_candidate(skill_ids={99})
    job = make_job(required_skill_ids={1, 2})
    result = score_candidate_job(candidate, job)
    # required_ratio=0 contributes 0; job has no preferred skills, so that
    # portion is trivially satisfied: 0.7*0 + 0.3*1.0 = 0.3, not 0.0.
    assert result.skill == 0.3
    assert result.missing_required_skill_ids == {1, 2}


def test_partial_required_match_is_proportional():
    candidate = make_candidate(skill_ids={1})
    job = make_job(required_skill_ids={1, 2})  # 1/2 required matched
    result = score_candidate_job(candidate, job)
    # 0.7 * 0.5 + 0.3 * 1.0 (no preferred skills -> trivially satisfied)
    assert result.skill == 0.65


def test_job_with_no_required_skills_is_trivially_satisfied():
    candidate = make_candidate(skill_ids=set())
    job = make_job(required_skill_ids=set())
    result = score_candidate_job(candidate, job)
    assert result.skill == 1.0


def test_same_city_scores_full_location_match():
    candidate = make_candidate(location_city="Metropolis", location_region="StateA")
    job = make_job(location_city="Metropolis", location_region="StateA")
    assert score_candidate_job(candidate, job).location == 1.0


def test_same_region_different_city_scores_partial_location_match():
    candidate = make_candidate(location_city="Smallville", location_region="StateA")
    job = make_job(location_city="Metropolis", location_region="StateA")
    assert score_candidate_job(candidate, job).location == 0.6


def test_different_region_scores_zero_location_match():
    candidate = make_candidate(location_city="Smallville", location_region="StateB")
    job = make_job(location_city="Metropolis", location_region="StateA")
    assert score_candidate_job(candidate, job).location == 0.0


def test_overlapping_salary_ranges_score_full_match():
    candidate = make_candidate(desired_salary_min=50000, desired_salary_max=80000)
    job = make_job(salary_min=70000, salary_max=100000)
    assert score_candidate_job(candidate, job).salary == 1.0


def test_non_overlapping_salary_ranges_score_zero():
    candidate = make_candidate(desired_salary_min=50000, desired_salary_max=60000)
    job = make_job(salary_min=100000, salary_max=120000)
    assert score_candidate_job(candidate, job).salary == 0.0


def test_missing_salary_data_scores_neutral_not_penalized():
    candidate = make_candidate(desired_salary_min=None, desired_salary_max=None)
    job = make_job(salary_min=70000, salary_max=100000)
    assert score_candidate_job(candidate, job).salary == UNKNOWN_DATA_SCORE


def test_same_occupation_scores_full_match():
    candidate = make_candidate(primary_occupation_id=42)
    job = make_job(occupation_id=42)
    assert score_candidate_job(candidate, job).occupation == 1.0


def test_different_occupation_scores_zero():
    candidate = make_candidate(primary_occupation_id=42)
    job = make_job(occupation_id=7)
    assert score_candidate_job(candidate, job).occupation == 0.0


def test_missing_occupation_data_scores_neutral():
    candidate = make_candidate(primary_occupation_id=None)
    job = make_job(occupation_id=42)
    assert score_candidate_job(candidate, job).occupation == UNKNOWN_DATA_SCORE
