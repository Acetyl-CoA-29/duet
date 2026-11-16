from module import allocate, describe


def test_happy_shares_add_up():
    shares = allocate(100, {"ann": 1, "ben": 1, "cy": 1})
    assert shares == {"ann": 34, "ben": 33, "cy": 33}
    assert allocate(1001, {"a": 3, "b": 1}) == {"a": 751, "b": 250}
    assert describe({"x": 5, "y": 250}) == ["y: 2.50", "x: 0.05"]
    assert allocate(500, {}) == {}


def test_defect_all_zero_weights_pay_nobody():
    assert allocate(500, {"ann": 0, "ben": 0}) == {}
