"""The dashboard projection intentionally accepts extension fields in a record."""

from typing import Any, Dict, Iterable

from .model import Record


def summarize(records: Iterable[Record]) -> Dict[str, Any]:
    counts = {}
    first = None
    last = None
    for record in records:
        kind = record.value.get("kind", "unknown")
        if not isinstance(kind, str):
            kind = "unknown"
        counts[kind] = counts.get(kind, 0) + 1
        if first is None:
            first = record.line
        last = record.line
    return {"counts": counts, "first_line": first, "last_line": last}
