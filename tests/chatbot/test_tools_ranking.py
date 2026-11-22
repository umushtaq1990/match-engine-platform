"""Regression tests for app.chatbot.tools.rank_by_criteria -- the 4th tool
that closes the "Candidate/job criteria search & ranking" Roadmap gap (see
docs/architecture/application-services/rag-chatbot.md): ranking across the
WHOLE candidate/job pool by skill criteria, which neither
lookup_structured_data (one named id) nor lookup_graph_context (adjacent to
one named id) can do.

Uses the real-transaction-wrapped `matching_db_session` fixture (tests/
conftest.py) with small, explicit fixture rows -- the synthetic golden
dataset's exact ids/names aren't predictable enough to assert exact ranking
order against.
"""
from datetime import datetime, timezone

from app.chatbot.tools import rank_by_criteria
from app.ingestion.source_models import (
    Candidate,
    CandidateSkill,
    EmploymentType,
    EvidenceSource,
    Job,
    JobSkill,
    ProficiencyLevel,
    RequirementLevel,
    Skill,
)

_NOW = datetime.now(timezone.utc)


def _make_candidate(session, *, id, full_name, years_experience):
    session.add(Candidate(
        id=id, full_name=full_name, email=f"{full_name.lower().replace(' ', '.')}@example.com",
        location_city="Testville", location_region="TS", years_experience=years_experience,
        created_at=_NOW,
    ))


def _link_candidate_skill(session, candidate_id, skill_id):
    session.add(CandidateSkill(
        candidate_id=candidate_id, skill_id=skill_id,
        proficiency=ProficiencyLevel.intermediate, evidence_source=EvidenceSource.self_reported,
    ))


def _make_job(session, *, id, title, company_name):
    session.add(Job(
        id=id, title=title, company_name=company_name, description_raw="test job",
        location_city="Testville", location_region="TS",
        employment_type=EmploymentType.full_time, posted_at=_NOW,
    ))


def _link_job_skill(session, job_id, skill_id):
    session.add(JobSkill(job_id=job_id, skill_id=skill_id, requirement_level=RequirementLevel.required))


def test_ranks_candidates_by_descending_matched_skill_count(matching_db_session):
    matching_db_session.add_all([
        Skill(id=1, name="Python"), Skill(id=2, name="AWS"), Skill(id=3, name="SQL"),
    ])
    _make_candidate(matching_db_session, id=1, full_name="Alice Best", years_experience=5)
    _make_candidate(matching_db_session, id=2, full_name="Bob Partial", years_experience=3)
    _make_candidate(matching_db_session, id=3, full_name="Carol None", years_experience=10)
    for skill_id in (1, 2, 3):
        _link_candidate_skill(matching_db_session, 1, skill_id)
    _link_candidate_skill(matching_db_session, 2, 1)
    matching_db_session.commit()

    result = rank_by_criteria(
        matching_db_session, "admin", "candidate", ["Python", "AWS", "SQL"],
    )

    assert result["matched_skill_names"] == ["AWS", "Python", "SQL"]
    ranked_ids = [row["id"] for row in result["ranked"]]
    assert ranked_ids == [1, 2]
    assert result["ranked"][0]["matched_skill_count"] == 3
    assert result["ranked"][0]["matched_skills"] == ["AWS", "Python", "SQL"]
    assert result["ranked"][1]["matched_skill_count"] == 1


def test_candidate_search_respects_min_years_experience(matching_db_session):
    matching_db_session.add(Skill(id=1, name="Go"))
    _make_candidate(matching_db_session, id=1, full_name="Junior Dev", years_experience=1)
    _make_candidate(matching_db_session, id=2, full_name="Senior Dev", years_experience=8)
    _link_candidate_skill(matching_db_session, 1, 1)
    _link_candidate_skill(matching_db_session, 2, 1)
    matching_db_session.commit()

    result = rank_by_criteria(
        matching_db_session, "admin", "candidate", ["Go"], min_years_experience=5,
    )

    assert [row["id"] for row in result["ranked"]] == [2]


def test_candidate_search_fails_closed_for_a_role_without_confidential_access(matching_db_session):
    matching_db_session.add(Skill(id=1, name="Python"))
    _make_candidate(matching_db_session, id=1, full_name="Alice", years_experience=5)
    _link_candidate_skill(matching_db_session, 1, 1)
    matching_db_session.commit()

    result = rank_by_criteria(matching_db_session, "candidate", "candidate", ["Python"])

    assert result == {}


def test_ranks_jobs_by_descending_matched_skill_count(matching_db_session):
    matching_db_session.add_all([Skill(id=1, name="Kubernetes"), Skill(id=2, name="Go")])
    _make_job(matching_db_session, id=1, title="Platform Engineer", company_name="Acme")
    _make_job(matching_db_session, id=2, title="Frontend Developer", company_name="Acme")
    _link_job_skill(matching_db_session, 1, 1)
    _link_job_skill(matching_db_session, 1, 2)
    _link_job_skill(matching_db_session, 2, 2)
    matching_db_session.commit()

    result = rank_by_criteria(matching_db_session, "candidate", "job", ["Kubernetes", "Go"])

    assert [row["id"] for row in result["ranked"]] == [1, 2]
    assert result["ranked"][0]["company_name"] == "Acme"
    assert result["ranked"][0]["matched_skills"] == ["Go", "Kubernetes"]


def test_unmatched_skill_names_return_empty_ranked_list_not_an_error(matching_db_session):
    matching_db_session.add(Skill(id=1, name="Python"))
    matching_db_session.commit()

    result = rank_by_criteria(matching_db_session, "admin", "candidate", ["Nonexistent Skill"])

    assert result == {"entity_type": "candidate", "matched_skill_names": [], "ranked": []}


def test_top_k_is_capped_and_respected(matching_db_session):
    matching_db_session.add(Skill(id=1, name="Python"))
    for i in range(1, 8):
        _make_candidate(matching_db_session, id=i, full_name=f"Candidate {i}", years_experience=i)
        _link_candidate_skill(matching_db_session, i, 1)
    matching_db_session.commit()

    result = rank_by_criteria(matching_db_session, "admin", "candidate", ["Python"], top_k=3)

    assert len(result["ranked"]) == 3


def test_unrecognized_entity_type_returns_empty_sentinel(matching_db_session):
    result = rank_by_criteria(matching_db_session, "admin", "course", ["Python"])

    assert result == {}
