"""DB-touching tests for app/golden/select.py's deterministic candidate
selection -- uses the same real-transaction-wrapped fixture pattern as
tests/matching/test_rank_db.py (conftest.py's matching_db_session).
"""
import datetime as dt

from app.golden import select as select_module
from app.golden.select import select_and_assign_golden_candidates, select_golden_candidate_ids
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


def test_select_and_assign_reassigns_occupation_and_matching_skills(matching_db_session, monkeypatch):
    tech = Occupation(code="OCC-TECH", title="Fake Tech Role", is_curated=True, domain="technology")
    trades = Occupation(code="OCC-TRADE", title="Fake Trade Role", is_curated=True, domain="skilled_trades")
    old_occ = Occupation(code="OCC-OLD", title="Old Role")
    matching_db_session.add_all([tech, trades, old_occ])

    tech_skills = [Skill(name=f"TechSkill{i}", category="tech") for i in range(5)]
    trade_skills = [Skill(name=f"TradeSkill{i}", category="trade") for i in range(5)]
    old_skills = [Skill(name=f"OldSkill{i}", category="old") for i in range(5)]
    matching_db_session.add_all(tech_skills + trade_skills + old_skills)
    matching_db_session.flush()

    monkeypatch.setattr(select_module, "NEW_TITLES", ["Fake Tech Role", "Fake Trade Role"])
    monkeypatch.setattr(
        select_module, "SKILL_DOMAINS", {"technology": {"tech"}, "skilled_trades": {"trade"}}
    )

    for candidate_id in range(1, 5):
        _make_candidate(matching_db_session, candidate_id, old_occ.id, n_skills=5, skills=old_skills)
    matching_db_session.commit()

    selected = select_and_assign_golden_candidates(matching_db_session, n=4)

    assert sorted(selected) == [1, 2, 3, 4]
    for i, candidate_id in enumerate(selected):
        candidate = matching_db_session.get(Candidate, candidate_id)
        expected_occ = tech if i % 2 == 0 else trades
        assert candidate.primary_occupation_id == expected_occ.id

        new_skills = matching_db_session.query(CandidateSkill).filter_by(candidate_id=candidate_id).all()
        assert new_skills, "candidate should have regenerated skills"
        new_skill_ids = {row.skill_id for row in new_skills}
        old_skill_ids = {s.id for s in old_skills}
        # Regenerated skills should not just be a straight copy of the old,
        # mismatched profile -- at least some overlap with the new domain's
        # own pool is expected (anchor majority), old skills aren't banned
        # outright since a small amount of cross-domain noise is by design.
        assert new_skill_ids - old_skill_ids, "some skills should come from the new domain's pool"
