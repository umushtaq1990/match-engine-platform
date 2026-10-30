from app.extraction.metrics import requirement_level_accuracy, set_f1, set_precision, set_recall


def test_precision_perfect_match():
    assert set_precision({1, 2}, {1, 2}) == 1.0


def test_precision_partial_match():
    assert set_precision({1, 2, 3}, {1, 2}) == 2 / 3


def test_precision_no_prediction_no_expectation_is_vacuously_correct():
    assert set_precision(set(), set()) == 1.0


def test_precision_no_prediction_but_expectation_exists():
    assert set_precision(set(), {1}) == 0.0


def test_recall_perfect_match():
    assert set_recall({1, 2}, {1, 2}) == 1.0


def test_recall_partial_match():
    assert set_recall({1}, {1, 2}) == 0.5


def test_recall_no_expectation_is_vacuously_correct():
    assert set_recall({1, 2}, set()) == 1.0


def test_f1_perfect_match():
    assert set_f1({1, 2}, {1, 2}) == 1.0


def test_f1_no_overlap():
    assert set_f1({1}, {2}) == 0.0


def test_f1_empty_both():
    assert set_f1(set(), set()) == 1.0


def test_requirement_level_accuracy_none_when_no_true_positives():
    assert requirement_level_accuracy({}, {}, set()) is None


def test_requirement_level_accuracy_all_correct():
    predicted = {1: "required", 2: "preferred"}
    expected = {1: "required", 2: "preferred"}
    assert requirement_level_accuracy(predicted, expected, {1, 2}) == 1.0


def test_requirement_level_accuracy_partial():
    predicted = {1: "required", 2: "required"}
    expected = {1: "required", 2: "preferred"}
    assert requirement_level_accuracy(predicted, expected, {1, 2}) == 0.5
