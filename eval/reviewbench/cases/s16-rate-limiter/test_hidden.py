from module import RateLimiter


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_happy_under_the_limit_and_window_expiry():
    clock = Clock()
    limiter = RateLimiter(limit=3, window=10, clock=clock)
    assert limiter.allow("a") and limiter.allow("a")
    assert limiter.remaining("a") == 1 and limiter.retry_after("a") == 0.0
    assert limiter.allow("b") and limiter.remaining("b") == 2
    clock.now += 11
    assert limiter.remaining("a") == 3


def test_defect_call_over_the_limit_is_refused():
    limiter = RateLimiter(limit=3, window=10, clock=Clock())
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
