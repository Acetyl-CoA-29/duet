"""Typed pipeline records. Names are catalog keys, never filesystem paths."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Job:
    name: str
    requires: Tuple[str, ...] = ()
    after: Tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class Plan:
    run: Tuple[str, ...]
    reused: Tuple[str, ...]


class MissingJob(ValueError):
    def __init__(self, name):
        self.name = name
        super().__init__("job is not in the catalog: " + name)


class DisabledJob(ValueError):
    def __init__(self, name):
        self.name = name
        super().__init__("job is disabled: " + name)


class CycleError(ValueError):
    def __init__(self, blocked):
        self.blocked = tuple(sorted(blocked))
        super().__init__("unschedulable jobs: " + ", ".join(self.blocked))
