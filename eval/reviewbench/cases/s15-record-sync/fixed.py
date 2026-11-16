"""Push locally changed records to the remote service and remember which ones made it."""
from typing import Any, Callable, Dict, FrozenSet, List, NamedTuple, Sequence, Tuple


class PushError(Exception):
    """The remote service refused or failed to store one record."""


class Record(NamedTuple):
    id: str
    payload: Dict[str, Any]


class SyncResult(NamedTuple):
    synced: FrozenSet[str]
    failures: List[Tuple[str, str]]
    attempted: int


def pending(records: Sequence[Record], synced: FrozenSet[str]) -> List[Record]:
    """Records that still need pushing, in their original order, each id once."""
    seen = set(synced)
    queue: List[Record] = []
    for record in records:
        if record.id not in seen:
            seen.add(record.id)
            queue.append(record)
    return queue


def sync(records: Sequence[Record], already_synced: FrozenSet[str],
         push: Callable[[Record], None]) -> SyncResult:
    """Push every record that is not yet synced and report the outcome."""
    queue = pending(records, already_synced)
    failures: List[Tuple[str, str]] = []
    for record in queue:
        try:
            push(record)
        except PushError as exc:
            failures.append((record.id, str(exc)))
    failed = frozenset(record_id for record_id, _ in failures)
    synced = already_synced | frozenset(record.id for record in queue if record.id not in failed)
    return SyncResult(synced, failures, len(queue))


def report(result: SyncResult) -> str:
    """A one-line summary for the log, followed by one line per failure."""
    pushed = result.attempted - len(result.failures)
    lines = [f"pushed {pushed} of {result.attempted} record(s); {len(result.synced)} synced in total"]
    lines.extend(f"  failed {record_id}: {reason}" for record_id, reason in result.failures)
    return "\n".join(lines)
