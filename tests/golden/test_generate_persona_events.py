"""Pure-logic tests for app/golden/generate_persona_events.py's persona
assignment -- no DB involved (segmentation-label correctness against the
real GET /actors/candidate/{id}/segment logic is checked live, not here --
see the plan's Verification section)."""
from app.golden.generate_persona_events import (
    PERSONA_WEIGHTS,
    assign_personas,
)


def test_every_candidate_gets_exactly_one_persona():
    candidate_ids = list(range(1, 501))
    assignment = assign_personas(candidate_ids)

    assert set(assignment.keys()) == set(candidate_ids)
    assert set(assignment.values()) <= set(PERSONA_WEIGHTS.keys())


def test_is_deterministic_for_a_fixed_seed():
    candidate_ids = list(range(1, 101))

    first = assign_personas(candidate_ids)
    second = assign_personas(candidate_ids)

    assert first == second


def test_distribution_roughly_matches_configured_weights():
    candidate_ids = list(range(1, 1001))
    assignment = assign_personas(candidate_ids)

    counts = {persona: 0 for persona in PERSONA_WEIGHTS}
    for persona in assignment.values():
        counts[persona] += 1

    for persona, weight in PERSONA_WEIGHTS.items():
        expected = weight * len(candidate_ids)
        assert abs(counts[persona] - expected) <= 5, (persona, counts[persona], expected)


def test_different_seeds_produce_different_assignments():
    candidate_ids = list(range(1, 101))

    assignment_a = assign_personas(candidate_ids, seed=1)
    assignment_b = assign_personas(candidate_ids, seed=2)

    assert assignment_a != assignment_b
