"""Sliding-window rate limiter: at most `limit` calls per client in any `window` seconds."""
import time
from collections import deque
from typing import Callable, Deque, Dict


class RateLimiter:
    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a whole number of at least 1")
        if window <= 0:
            raise ValueError("window must be positive")
        self._limit = limit
        self._window = window
        self._clock = clock
        self._calls: Dict[str, Deque[float]] = {}

    def _recent(self, client: str, now: float) -> Deque[float]:
        """The client's calls that are still inside the window, oldest first."""
        calls = self._calls.setdefault(client, deque())
        while calls and calls[0] <= now - self._window:
            calls.popleft()
        return calls

    def allow(self, client: str) -> bool:
        """Record a call and return True when the client is within its limit; otherwise return
        False and record nothing."""
        now = self._clock()
        calls = self._recent(client, now)
        if len(calls) > self._limit:
            return False
        calls.append(now)
        return True

    def remaining(self, client: str) -> int:
        """How many more calls the client may make right now."""
        return max(0, self._limit - len(self._recent(client, self._clock())))

    def retry_after(self, client: str) -> float:
        """Seconds until the client may call again; 0.0 when it may call now."""
        now = self._clock()
        calls = self._recent(client, now)
        if len(calls) < self._limit:
            return 0.0
        return max(0.0, calls[0] + self._window - now)

    def forget(self, client: str) -> None:
        self._calls.pop(client, None)
