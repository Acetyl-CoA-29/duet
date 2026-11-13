"""A small least-recently-used cache whose entries expire."""
import time
from collections import OrderedDict

_MISSING = object()


class TTLCache:
    """Least-recently-used cache with a time to live.

    Contract:

    * ``capacity`` and ``ttl`` must be greater than zero, otherwise ``ValueError``.
    * An entry is live while ``clock() - stored_at < ttl``. At exactly ``ttl`` it has expired.
    * ``get(key, default=None)`` returns the value of a live entry and makes it the most
      recently used. For a missing or expired key it returns ``default``; an expired entry
      is removed.
    * ``put(key, value)`` on a key that is already present replaces the value, restarts its
      time to live and makes it the most recently used. It never evicts another entry.
    * ``put`` of a new key when the cache is full first drops every expired entry. Only if the
      cache is still full does it evict the least recently used entry.
    * ``len(cache)`` counts live entries only.
    * ``key in cache`` is true for live entries only and does not change which entry is the
      least recently used.
    """

    def __init__(self, capacity, ttl, clock=time.monotonic):
        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        self._items = OrderedDict()  # key -> (value, stored_at), least recently used first

    def _expired(self, stored_at):
        return self._clock() - stored_at > self._ttl

    def get(self, key, default=None):
        if key not in self._items:
            return default
        value, stored_at = self._items[key]
        if self._expired(stored_at):
            del self._items[key]
            return default
        return value

    def put(self, key, value):
        if len(self._items) >= self._capacity:
            self._items.popitem(last=False)
        self._items[key] = (value, self._clock())

    def __len__(self):
        return len(self._items)

    def __contains__(self, key):
        return self.get(key, _MISSING) is not _MISSING
