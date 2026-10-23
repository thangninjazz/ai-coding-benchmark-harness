import math

import pytest

from harness.metrics import aggregate_pass_at_k, pass_at_k


def test_pass_at_1_is_the_empirical_rate():
    assert pass_at_k(10, 3, 1) == pytest.approx(0.3)


def test_pass_at_k_matches_closed_form():
    n, c, k = 20, 4, 5
    expected = 1 - math.comb(n - c, k) / math.comb(n, k)
    assert pass_at_k(n, c, k) == pytest.approx(expected)


def test_boundaries():
    assert pass_at_k(5, 0, 3) == 0.0
    assert pass_at_k(5, 5, 3) == 1.0
    assert pass_at_k(5, 3, 3) == 1.0  # fewer than k failures: always at least one pass
    assert pass_at_k(1, 1, 1) == 1.0


@pytest.mark.parametrize("n,c,k", [(0, 0, 1), (5, 6, 1), (5, -1, 1), (5, 2, 0), (5, 2, 6)])
def test_invalid_arguments(n, c, k):
    with pytest.raises(ValueError):
        pass_at_k(n, c, k)


def test_aggregate_averages_over_tasks():
    scores = aggregate_pass_at_k({"a": [True, False], "b": [False, False]}, ks=[1, 2])
    assert scores[1] == pytest.approx(0.25)
    assert scores[2] == pytest.approx(0.5)


def test_aggregate_requires_enough_samples():
    with pytest.raises(ValueError):
        aggregate_pass_at_k({"a": [True]}, ks=[2])
    with pytest.raises(ValueError):
        aggregate_pass_at_k({})
