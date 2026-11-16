import pytest

from module import contains, covered_length, gaps, merge


def test_happy_merge_measure_and_gaps():
    slots = [(30, 60), (0, 10), (10, 20), (50, 90), (95, 95)]
    assert merge(slots) == [(0, 20), (30, 90), (95, 95)]
    assert covered_length(slots) == 80
    assert contains(slots, 20) and not contains(slots, 25)
    assert gaps(slots, 0, 100) == [(20, 30), (90, 95), (95, 100)]
    assert gaps([(0, 100)], 10, 20) == [] and gaps([], 5, 9) == [(5, 9)]
    assert merge([]) == []
    with pytest.raises(ValueError):
        merge([(5, 1)])
