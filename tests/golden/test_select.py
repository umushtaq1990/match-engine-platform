"""DB-touching tests for app/golden/select.py's deterministic candidate
selection -- uses the same real-transaction-wrapped fixture pattern as
tests/matching/test_rank_db.py (conftest.py's matching_db_session).
"""
import datetime as dt

from app.golden.select import select_golden_candidate_ids
from app.ingestion.source_models import (
    Candidate,
    CandidateSkill,
    EvidenceSource,
    Occupation,
    ProficiencyLevel,
    Skill,
)


def _make_candidate(session, candidate_id, occupation_id, n_skills, skills):
    session.add(Candidate(
        id=candidate_id, full_name=f"Candidate {candidate_id}",
        email=f"candidate{candidate_id}@example-synthetic.test",
        location_city="Austin", location_region="TX", years_experience=3,
        primary_occupation_id=occupation_id,
        created_at=dt.datetime.now(dt.timezone.utc),
    ))
    session.flush()
    for skill in skills[:n_skills]:
        session.add(CandidateSkill(
            candidate_id=candidate_id, skill_id=skill.id,
            proficiency=ProficiencyLevel.intermediate, evidence_source=EvidenceSource.self_reported,
        ))


def test_excludes_candidates_with_no_occupation_or_too_few_skills(matching_db_session):
    occ = Occupation(code="OCC-1", title="Engineer")
    matching_db_session.add(occ)
    skills = [Skill(name=f"Skill{i}") for i in range(5)]
    matching_db_session.add_all(skills)
    matching_db_session.flush()

    _make_candidate(matching_db_session, 1, occ.id, n_skills=5, skills=skills)  # eligible
    _make_candidate(matching_db_session, 2, occ.id, n_skills=1, skills=skills)  # too few skills
    _make_candidate(matching_db_session, 3, None, n_skills=5, skills=skills)  # no occupation
    matching_db_session.commit()

    ids = select_golden_candidate_ids(matching_db_session, n=10)

    assert ids == [1]


def test_is_deterministic_across_calls(matching_db_session):
    occ = Occupation(code="OCC-1", title="Engineer")
    matching_db_session.add(occ)
    skills = [Skill(name=f"Skill{i}") for i in range(5)]
    matching_db_session.add_all(skills)
    matching_db_session.flush()
    for candidate_id in range(1, 11):
        _make_candidate(matching_db_session, candidate_id, occ.id, n_skills=5, skills=skills)
    matching_db_session.commit()

    first = select_golden_candidate_ids(matching_db_session, n=5)
    second = select_golden_candidate_ids(matching_db_session, n=5)

    assert first == second
    assert len(first) == 5
    assert len(set(first)) == 5


def test_returns_all_eligible_when_fewer_than_n(matching_db_session):
    occ = Occupation(code="OCC-1", title="Engineer")
    matching_db_session.add(occ)
    skills = [Skill(name=f"Skill{i}") for i in range(5)]
    matching_db_session.add_all(skills)
    matching_db_session.flush()
    for candidate_id in range(1, 4):
        _make_candidate(matching_db_session, candidate_id, occ.id, n_skills=5, skills=skills)
    matching_db_session.commit()

    ids = select_golden_candidate_ids(matching_db_session, n=500)

    assert sorted(ids) == [1, 2, 3]


def test_spreads_evenly_across_occupations_when_capped(matching_db_session):
    occ_a = Occupation(code="OCC-A", title="Engineer")
    occ_b = Occupation(code="OCC-B", title="Nurse")
    matching_db_session.add_all([occ_a, occ_b])
    skills = [Skill(name=f"Skill{i}") for i in range(5)]
    matching_db_session.add_all(skills)
    matching_db_session.flush()
    for candidate_id in range(1, 5):
        _make_candidate(matching_db_session, candidate_id, occ_a.id, n_skills=5, skills=skills)
    for candidate_id in range(5, 9):
        _make_candidate(matching_db_session, candidate_id, occ_b.id, n_skills=5, skills=skills)
    matching_db_session.commit()

    ids = select_golden_candidate_ids(matching_db_session, n=4)

    assert len(ids) == 4
    from_a = sum(1 for i in ids if i < 5)
    from_b = sum(1 for i in ids if i >= 5)
    assert from_a == 2 and from_b == 2
