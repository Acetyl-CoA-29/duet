import pytest

from cache import TTLCache


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(capacity=2, ttl=10):
    clock = FakeClock()
    return TTLCache(capacity=capacity, ttl=ttl, clock=clock), clock


def test_get_refreshes_recency():
    cache, _ = make()
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("a") == 1
    assert cache.get("b") is None


def test_entry_expires_at_exactly_ttl():
    cache, clock = make()
    cache.put("a", 1)
    clock.now = 9.999
    assert cache.get("a") == 1
    clock.now = 10
    assert cache.get("a") is None
    assert cache.get("a", "gone") == "gone"


def test_updating_existing_key_never_evicts():
    cache, _ = make()
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("b", 22)
    assert cache.get("a") == 1
    assert cache.get("b") == 22
    assert len(cache) == 2


def test_updating_existing_key_makes_it_most_recent():
    cache, _ = make()
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 11)
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 11


def test_updating_restarts_time_to_live():
    cache, clock = make()
    cache.put("a", 1)
    clock.now = 8
    cache.put("a", 2)
    clock.now = 15
    assert cache.get("a") == 2


def test_reading_does_not_restart_time_to_live():
    cache, clock = make()
    cache.put("a", 1)
    clock.now = 8
    assert cache.get("a") == 1
    clock.now = 12
    assert cache.get("a") is None


def test_expired_entries_are_dropped_before_evicting_live_ones():
    cache, clock = make()
    cache.put("old", 1)
    clock.now = 5
    cache.put("live", 2)
    clock.now = 6
    assert cache.get("old") == 1    # "live" is now the least recently used
    clock.now = 11                  # "old" has expired, "live" has not
    cache.put("new", 3)
    assert cache.get("live") == 2
    assert cache.get("new") == 3
    assert cache.get("old") is None


def test_len_counts_only_live_entries():
    cache, clock = make(capacity=3)
    cache.put("a", 1)
    clock.now = 5
    cache.put("b", 2)
    assert len(cache) == 2
    clock.now = 12
    assert len(cache) == 1
    clock.now = 100
    assert len(cache) == 0


def test_contains_is_true_only_for_live_entries():
    cache, clock = make()
    cache.put("a", 1)
    assert "a" in cache
    assert "b" not in cache
    clock.now = 10
    assert "a" not in cache


def test_contains_does_not_change_recency():
    cache, _ = make()
    cache.put("a", 1)
    cache.put("b", 2)
    assert "a" in cache             # must not rescue "a"
    cache.put("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2


def test_stored_none_is_distinguishable_with_default():
    cache, _ = make()
    cache.put("a", None)
    assert "a" in cache
    assert cache.get("a", "fallback") is None


@pytest.mark.parametrize("capacity,ttl", [(0, 10), (-1, 10), (2, 0), (2, -5)])
def test_constructor_validates(capacity, ttl):
    with pytest.raises(ValueError):
        TTLCache(capacity=capacity, ttl=ttl)


def test_capacity_one():
    cache, _ = make(capacity=1)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert len(cache) == 1
