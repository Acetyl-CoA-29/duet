import pytest

from module import GaveUp, RateLimited, TransientError, call_with_retry


def test_happy_transient_errors_then_success_or_give_up():
    waits, outcomes = [], [TransientError("a"), TransientError("b"), "done"]

    def flaky():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert call_with_retry(flaky, attempts=4, base_delay=1.0, sleep=waits.append) == "done"
    assert waits == [1.0, 2.0]

    def broken():
        raise TransientError("down")

    with pytest.raises(GaveUp):
        call_with_retry(broken, attempts=3, sleep=waits.append)
    assert len(waits) == 4


def test_defect_rate_limited_forever_gives_up():
    waits = []

    def record_wait(seconds):
        waits.append(seconds)
        if len(waits) > 50:
            raise RuntimeError("still retrying after 50 waits")

    def limited():
        raise RateLimited(2.0)

    with pytest.raises(GaveUp):
        call_with_retry(limited, attempts=3, sleep=record_wait)
    assert waits == [2.0, 2.0]
