"""A small least-recently-used cache for single-threaded use."""
from collections import OrderedDict
from typing import Any, Hashable, List

_MISSING = object()


class LruCache:
    def __init__(self, capacity: int) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a whole number of at least 1")
        self._capacity = capacity
        self._items: "OrderedDict[Hashable, Any]" = OrderedDict()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._items

    def get(self, key: Hashable, default: Any = None) -> Any:
        """The value stored for `key`, which becomes the most recently used; `default` when absent."""
        value = self._items.get(key, _MISSING)
        if value is _MISSING:
            return default
        self._items.move_to_end(key)
        return value

    def put(self, key: Hashable, value: Any) -> None:
        """Store `value` for `key`, dropping the least recently used key if the cache is over capacity."""
        self._items[key] = value
        self._items.move_to_end(key)
        if len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def discard(self, key: Hashable) -> bool:
        """Forget `key`. Returns True when it was present."""
        if key not in self._items:
            return False
        del self._items[key]
        return True

    def keys(self) -> List[Hashable]:
        """Keys from least to most recently used."""
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
