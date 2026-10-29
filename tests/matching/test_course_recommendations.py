from app.matching.course_recommendations import (
    analyze_occupation_skill_gap,
    recommend_courses,
)
from tests.matching.test_scoring import make_candidate


def test_skills_candidate_already_has_are_present_not_gap():
    candidate = make_candidate(skill_ids={1, 2})
    profile = {1: 1.0, 2: 0.8, 3: 0.5}
    gap = analyze_occupation_skill_gap(candidate, occupation_id=10, occupation_skill_profile=profile)
    assert gap.present == {1: 1.0, 2: 0.8}
    assert 3 in gap.not_recorded
    assert gap.total_gap_weight == 0.5


def test_confirmed_absent_vs_not_recorded_split():
    candidate = make_candidate(skill_ids=set(), confirmed_absent_skill_ids={2})
    profile = {1: 1.0, 2: 0.9}
    gap = analyze_occupation_skill_gap(candidate, occupation_id=10, occupation_skill_profile=profile)
    assert gap.confirmed_absent == {2: 0.9}
    assert gap.not_recorded == {1: 1.0}


def test_no_gap_returns_no_recommendations():
    candidate = make_candidate(skill_ids={1, 2})
    profile = {1: 1.0, 2: 1.0}
    gap = analyze_occupation_skill_gap(candidate, occupation_id=10, occupation_skill_profile=profile)
    recs = recommend_courses(gap, course_skill_coverage={99: {1: 1.0}}, course_titles={})
    assert recs == []


def test_course_covering_higher_importance_gap_ranks_first():
    candidate = make_candidate(skill_ids=set())
    profile = {1: 1.0, 2: 0.2}  # skill 1 much more important to this occupation
    gap = analyze_occupation_skill_gap(candidate, occupation_id=10, occupation_skill_profile=profile)

    coverage = {
        100: {1: 1.0},  # covers the high-importance gap fully
        200: {2: 1.0},  # covers only the low-importance gap
    }
    recs = recommend_courses(gap, coverage, course_titles={100: "Course A", 200: "Course B"})
    assert recs[0].course_id == 100
    assert recs[0].coverage_score > recs[1].coverage_score


def test_course_covering_no_gap_skills_is_excluded():
    candidate = make_candidate(skill_ids=set())
    profile = {1: 1.0}
    gap = analyze_occupation_skill_gap(candidate, occupation_id=10, occupation_skill_profile=profile)
    coverage = {100: {99: 1.0}}  # covers an unrelated skill only
    recs = recommend_courses(gap, coverage, course_titles={})
    assert recs == []


def test_partial_coverage_score_is_proportional():
    candidate = make_candidate(skill_ids=set())
    profile = {1: 1.0, 2: 1.0}  # total gap weight = 2.0
    gap = analyze_occupation_skill_gap(candidate, occupation_id=10, occupation_skill_profile=profile)
    coverage = {100: {1: 1.0}}  # covers half the total gap weight
    recs = recommend_courses(gap, coverage, course_titles={100: "Course A"})
    assert recs[0].coverage_score == 0.5
