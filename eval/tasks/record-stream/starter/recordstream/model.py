"""Shared value types. These are also used by consumers outside this package."""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class Record:
    line: int
    value: Dict[str, Any]


class RecordError(ValueError):
    """Public, content-free parse failure suitable for a request log."""

    def __init__(self, line: int, kind: str):
        self.line = line
        self.kind = kind
        super().__init__("invalid {} record on line {}".format(kind, line))
