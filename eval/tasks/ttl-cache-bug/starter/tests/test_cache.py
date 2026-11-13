from cache import TTLCache


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_put_and_get():
    cache = TTLCache(capacity=2, ttl=10, clock=FakeClock())
    cache.put("a", 1)
    assert cache.get("a") == 1
    assert cache.get("missing") is None
    assert cache.get("missing", "fallback") == "fallback"


def test_least_recently_used_is_evicted():
    cache = TTLCache(capacity=2, ttl=10, clock=FakeClock())
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_recently_read_entry_survives():
    cache = TTLCache(capacity=2, ttl=10, clock=FakeClock())
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1      # "a" is now the most recently used
    cache.put("c", 3)               # so "b" is the one to go
    assert cache.get("a") == 1
    assert cache.get("b") is None


def test_old_entries_expire():
    clock = FakeClock()
    cache = TTLCache(capacity=2, ttl=10, clock=clock)
    cache.put("a", 1)
    clock.now = 25
    assert cache.get("a") is None
