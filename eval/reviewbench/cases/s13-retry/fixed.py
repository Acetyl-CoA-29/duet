"""Call a flaky remote operation again when it fails in a way that is worth retrying."""
import time
from typing import Callable, TypeVar

T = TypeVar("T")
MAX_DELAY_SECONDS = 30.0


class TransientError(Exception):
    """The operation failed but may work if tried again."""


class RateLimited(Exception):
    """The server asked the caller to slow down."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited; retry after {retry_after:g}s")
        self.retry_after = retry_after


class GaveUp(Exception):
    """Every attempt failed."""


def backoff_delay(failures: int, base_delay: float) -> float:
    """Seconds to wait after the given number of failures: base, 2x base, 4x base, ... capped."""
    return min(MAX_DELAY_SECONDS, base_delay * 2 ** (failures - 1))


def call_with_retry(operation: Callable[[], T], attempts: int = 4, base_delay: float = 0.5,
                    sleep: Callable[[float], None] = time.sleep) -> T:
    """Run `operation` and return its result, retrying on TransientError and RateLimited."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if base_delay < 0:
        raise ValueError("base_delay cannot be negative")
    failures = 0
    while True:
        try:
            return operation()
        except RateLimited as exc:
            failures += 1
            if failures >= attempts:
                raise GaveUp(f"the operation failed {failures} times") from exc
            sleep(min(MAX_DELAY_SECONDS, max(0.0, exc.retry_after)))
        except TransientError as exc:
            failures += 1
            if failures >= attempts:
                raise GaveUp(f"the operation failed {failures} times") from exc
            sleep(backoff_delay(failures, base_delay))
