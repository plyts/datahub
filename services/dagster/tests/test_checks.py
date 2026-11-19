import pytest

from datahub_pipelines.checks import zscore


def test_zscore_needs_history():
    assert zscore(100, [1, 2, 3]) == 0.0


def test_zscore_flat_history():
    assert zscore(100, [10.0] * 10) == 0.0


@pytest.mark.parametrize(("value", "expected_sign"), [(200, 1), (0, -1)])
def test_zscore_detects_outliers(value, expected_sign):
    history = [80, 82, 78, 81, 79, 80, 83, 77, 80, 81]
    score = zscore(value, history)
    assert abs(score) > 3
    assert (score > 0) == (expected_sign > 0)


def test_zscore_normal_value():
    assert abs(zscore(80, [80, 82, 78, 81, 79, 80, 83, 77, 80, 81])) < 1
