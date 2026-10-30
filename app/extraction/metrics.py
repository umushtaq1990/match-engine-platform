"""Extraction-accuracy metrics (Use Case C, Phase 16) — pure functions, no
DB/LLM involved, unit-testable in isolation. Different in kind from
app/evaluation/metrics.py (which measures *ranking* quality): this measures
*extraction* accuracy, a set-comparison problem, not a ranked-list one.
"""


def set_precision(predicted: set[int], expected: set[int]) -> float:
    if not predicted:
        return 1.0 if not expected else 0.0
    return len(predicted & expected) / len(predicted)


def set_recall(predicted: set[int], expected: set[int]) -> float:
    if not expected:
        return 1.0
    return len(predicted & expected) / len(expected)


def set_f1(predicted: set[int], expected: set[int]) -> float:
    precision = set_precision(predicted, expected)
    recall = set_recall(predicted, expected)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def requirement_level_accuracy(
    predicted_levels: dict[int, str], expected_levels: dict[int, str], true_positive_ids: set[int]
) -> float | None:
    """Of the skills correctly identified (true positives only), what
    fraction got required/preferred right. None when there are no true
    positives to score agreement on — not zero, which would conflate "no
    skills matched" with "matched skills, but all levels wrong"."""
    if not true_positive_ids:
        return None
    correct = sum(
        1 for skill_id in true_positive_ids
        if predicted_levels.get(skill_id) == expected_levels.get(skill_id)
    )
    return correct / len(true_positive_ids)
