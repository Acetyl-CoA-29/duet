from billing import allocate


def test_even_split():
    assert allocate(100, [1, 1]) == [50, 50]


def test_weighted_split():
    assert allocate(100, [1, 3]) == [25, 75]


def test_shares_add_up():
    assert sum(allocate(100, [1, 1, 1])) == 100


def test_one_share_per_weight():
    assert len(allocate(999, [5, 2, 2, 1])) == 4
