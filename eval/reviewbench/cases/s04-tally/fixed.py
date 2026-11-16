"""A tally of named counters shared by request-handling threads."""
import threading
from typing import Callable, Dict, List, Mapping, Optional, Tuple


class Tally:
    def __init__(self, initial: Optional[Mapping[str, int]] = None) -> None:
        self._lock = threading.Lock()
        self._values: Dict[str, int] = dict(initial or {})

    def get(self, name: str) -> int:
        with self._lock:
            return self._values.get(name, 0)

    def update(self, name: str, change: Callable[[int], int]) -> int:
        """Replace the counter with `change(current value)` and return the new value.
        `change` must not call back into this tally."""
        with self._lock:
            new_value = change(self._values.get(name, 0))
            if isinstance(new_value, bool) or not isinstance(new_value, int):
                raise TypeError("a counter holds whole numbers")
            self._values[name] = new_value
        return new_value

    def add(self, name: str, amount: int = 1) -> int:
        return self.update(name, lambda value: value + amount)

    def reset(self, name: str) -> int:
        """Set the counter back to zero and return the value it had."""
        with self._lock:
            return self._values.pop(name, 0)

    def merge(self, other: Mapping[str, int]) -> None:
        """Add every count in `other` to this tally."""
        with self._lock:
            for name, amount in other.items():
                self._values[name] = self._values.get(name, 0) + amount

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._values)

    def top(self, count: int) -> List[Tuple[str, int]]:
        """The `count` largest counters, largest first; ties in name order."""
        if count < 0:
            raise ValueError("count cannot be negative")
        ranked = sorted(self.snapshot().items(), key=lambda item: (-item[1], item[0]))
        return ranked[:count]
