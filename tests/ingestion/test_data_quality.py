from app.ingestion.data_quality import _status_from_count, _status_from_rate
from app.ingestion.quality_models import CheckStatus


def test_status_from_rate_pass_below_warn():
    assert _status_from_rate(0.05, warn=0.10, fail=0.20) == CheckStatus.pass_


def test_status_from_rate_warn_at_boundary():
    assert _status_from_rate(0.10, warn=0.10, fail=0.20) == CheckStatus.warn


def test_status_from_rate_fail_at_boundary():
    assert _status_from_rate(0.20, warn=0.10, fail=0.20) == CheckStatus.fail


def test_status_from_rate_fail_above_fail_threshold():
    assert _status_from_rate(0.99, warn=0.10, fail=0.20) == CheckStatus.fail


def test_status_from_count_zero_is_pass():
    assert _status_from_count(0) == CheckStatus.pass_


def test_status_from_count_nonzero_is_fail():
    assert _status_from_count(1) == CheckStatus.fail
