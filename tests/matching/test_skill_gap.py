from app.matching.skill_gap import analyze_skill_gap
from tests.matching.test_scoring import make_candidate, make_job


def test_missing_required_skill_with_no_assessment_is_not_recorded():
    candidate = make_candidate(skill_ids=set(), confirmed_absent_skill_ids=set())
    job = make_job(required_skill_ids={1})
    gap = analyze_skill_gap(candidate, job)
    assert gap.not_recorded_required == {1}
    assert gap.confirmed_absent_required == set()


def test_missing_required_skill_with_explicit_absent_assessment_is_confirmed_absent():
    candidate = make_candidate(skill_ids=set(), confirmed_absent_skill_ids={1})
    job = make_job(required_skill_ids={1})
    gap = analyze_skill_gap(candidate, job)
    assert gap.confirmed_absent_required == {1}
    assert gap.not_recorded_required == set()


def test_present_skill_is_not_a_gap_even_if_also_marked_absent_elsewhere():
    # Candidate has skill 1 (positive evidence) even though skill 2 was
    # separately assessed absent — only the actually-missing skill (2) is a
    # gap; having positive evidence for 1 means it's not missing at all.
    candidate = make_candidate(skill_ids={1}, confirmed_absent_skill_ids={2})
    job = make_job(required_skill_ids={1, 2})
    gap = analyze_skill_gap(candidate, job)
    assert gap.confirmed_absent_required == {2}
    assert 1 not in gap.confirmed_absent_required
    assert 1 not in gap.not_recorded_required
