import pytest

from billing import allocate


def test_exact_splits():
    assert allocate(100, [1, 1]) == [50, 50]
    assert allocate(100, [1, 3]) == [25, 75]
    assert allocate(0, [1, 2]) == [0, 0]


def test_leftover_goes_to_earlier_on_tie():
    assert allocate(100, [1, 1, 1]) == [34, 33, 33]
    assert allocate(5, [1, 1, 1]) == [2, 2, 1]


def test_leftover_goes_to_largest_fraction():
    assert allocate(100, [3, 3, 1]) == [43, 43, 14]
    assert allocate(10, [1, 2, 1]) == [3, 5, 2]
    assert allocate(100, [1, 2, 4]) == [14, 29, 57]


def test_zero_weight_gets_nothing():
    assert allocate(1, [0, 1, 0]) == [0, 1, 0]
    assert allocate(101, [1, 0, 1]) == [51, 0, 50]


def test_refund_mirrors_charge():
    assert allocate(-100, [1, 1, 1]) == [-34, -33, -33]
    assert allocate(-10, [1, 2, 1]) == [-3, -5, -2]


def test_large_totals_are_exact():
    big = 10 ** 18 + 1
    assert allocate(big, [1, 1]) == [5 * 10 ** 17 + 1, 5 * 10 ** 17]
    shares = allocate(10 ** 20 + 7, [3, 3, 3])
    assert sum(shares) == 10 ** 20 + 7
    assert max(shares) - min(shares) <= 1


@pytest.mark.parametrize("total,weights", [(1, [1] * 7), (999, [5, 2, 2, 1]), (12345, [7, 11, 13, 0, 1])])
def test_sum_is_always_exact(total, weights):
    assert sum(allocate(total, weights)) == total


@pytest.mark.parametrize("weights", [[], [0, 0], [1, -1]])
def test_bad_weights(weights):
    with pytest.raises(ValueError):
        allocate(100, weights)


@pytest.mark.parametrize("total,weights", [(1.5, [1]), ("100", [1]), (100, [1.0, 1]), (100, [True, 1]), (True, [1])])
def test_wrong_types(total, weights):
    with pytest.raises(TypeError):
        allocate(total, weights)


def test_weights_are_not_modified():
    weights = [3, 1, 2]
    allocate(100, weights)
    assert weights == [3, 1, 2]
