import pytest

from module import LruCache


def test_happy_eviction_order():
    cache = LruCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.keys() == ["a", "c"] and "b" not in cache and len(cache) == 2
    cache.put("a", 10)
    assert cache.keys() == ["c", "a"] and cache.get("zzz", "none") == "none"
    assert cache.discard("c") and not cache.discard("c")
    cache.put("n", None)
    assert cache.get("n", "fallback") is None
    with pytest.raises(ValueError):
        LruCache(0)
