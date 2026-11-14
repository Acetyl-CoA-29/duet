"""Turn run records into per-arm counts. Formatting lives in report.py.

Nothing is dropped quietly: every record under `records/` is counted, reruns set aside by the
runner are reported as a number, and sessions are kept apart unless the caller merges them."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import usage

Record = Dict[str, Any]
RECORDS_FOLDER = "records"
SUPERSEDED = "superseded"
MANIFEST = "session.json"
LIMIT_FIELDS = ("timeout", "duet_timeout", "max_cost", "effort", "duet_turns")
LEGACY_LIMIT_FIELDS = ("timeout", "max_cost", "effort")   # records from before sessions recorded their limits
Z_95 = 1.959964


@dataclass(frozen=True)
class Session:
    name: str
    records: List[Record]
    superseded: int = 0                     # earlier attempts set aside by a rerun; not counted
    stopped_early: List[str] = field(default_factory=list)


def _load_record(path: Path) -> Optional[Record]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: unreadable record ({exc})") from exc
    return value if isinstance(value, dict) and "arm" in value and "task" in value else None


def _stopped_early(session_dir: Path) -> List[str]:
    """Reasons recorded by invocations that ended before their plan was done."""
    try:
        manifest = json.loads((session_dir / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    invocations = manifest.get("invocations") if isinstance(manifest, dict) else None
    return [str(item["stopped_early"]) for item in invocations or []
            if isinstance(item, dict) and item.get("stopped_early")]


def _load_session(session_dir: Path) -> Session:
    folder = session_dir / RECORDS_FOLDER
    loaded = [_load_record(path) for path in sorted(folder.glob("*.json"))]
    records = [{**record, "eval_session": session_dir.name} for record in loaded if record is not None]
    aside = folder / SUPERSEDED
    superseded = len(list(aside.glob("*.json"))) if aside.is_dir() else 0
    return Session(session_dir.name, records, superseded, _stopped_early(session_dir))


def load_sessions(root: Path) -> Dict[str, Session]:
    """Sessions under a results root, or the one session `root` itself is, by name. Unreadable
    records are named, not skipped silently."""
    if (root / RECORDS_FOLDER).is_dir():
        folders = [root]
    else:
        folders = sorted(p for p in root.iterdir() if (p / RECORDS_FOLDER).is_dir()) if root.is_dir() else []
    return {folder.name: _load_session(folder) for folder in folders}


def limits_key(record: Record) -> Tuple[Any, ...]:
    """The limits a record was run under, in a form that can be compared across sessions."""
    shared = record.get("session_limits")
    if isinstance(shared, dict):
        return tuple(shared.get(name) for name in LIMIT_FIELDS)
    own = record.get("limits") or {}
    return tuple(own.get(name) for name in LEGACY_LIMIT_FIELDS)


def distinct_limits(records: Iterable[Record]) -> List[Tuple[Any, ...]]:
    return sorted({limits_key(record) for record in records}, key=repr)


def wilson(hits: int, total: int) -> Optional[Tuple[float, float]]:
    """95% Wilson interval for a proportion; None when there are no runs."""
    if total <= 0:
        return None
    share = hits / total
    spread = Z_95 * math.sqrt(share * (1 - share) / total + Z_95 ** 2 / (4 * total ** 2))
    centre, scale = share + Z_95 ** 2 / (2 * total), 1 + Z_95 ** 2 / total
    low, high = round((centre - spread) / scale, 10), round((centre + spread) / scale, 10)
    return max(0.0, low), min(1.0, high)


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _numbers(records: Sequence[Record], name: str) -> List[float]:
    return [float(r[name]) for r in records if isinstance(r.get(name), (int, float)) and not isinstance(r.get(name), bool)]


def passed(record: Record, which: str) -> bool:
    return bool((record.get(which) or {}).get("passed"))


def is_infra(record: Record) -> bool:
    """True when the run says nothing about the model: the tooling failed, not the work."""
    return bool(record.get("infra_failure")) or record.get("status") == "not_started"


def per_protocol(record: Record) -> bool:
    """A duet that tested the duet as designed: the peer took part and the run finished."""
    duet = record.get("duet") or {}
    turns = duet.get("peer_turns")
    return duet.get("finished") is True and isinstance(turns, int) and not isinstance(turns, bool) and turns > 0


def host_tokens(record: Record) -> Optional[int]:
    stored = record.get("host_tokens")
    if isinstance(stored, (int, float)) and not isinstance(stored, bool):
        return int(stored)
    return usage.total_tokens(record.get("tokens") or {})


def arm_stats(records: Sequence[Record]) -> Dict[str, Any]:
    """Counts for one arm. Pass counts and means are over valid runs: every run except
    infrastructure failures, which are counted on their own."""
    valid = [r for r in records if not is_infra(r)]
    duets = [r for r in valid if r.get("is_duet")]
    protocol = [r for r in duets if per_protocol(r)]
    hidden = sum(1 for r in valid if passed(r, "hidden"))
    costs, host = _numbers(valid, "cost_usd"), [t for t in (host_tokens(r) for r in valid) if t is not None]
    return {
        "runs": len(records), "infra": len(records) - len(valid), "valid": len(valid),
        "timeouts": sum(1 for r in records if r.get("timed_out")),
        "hidden_pass": hidden, "hidden_ci": wilson(hidden, len(valid)),
        "visible_pass": sum(1 for r in valid if passed(r, "visible")),
        "false_done": sum(1 for r in valid if passed(r, "visible") and not passed(r, "hidden")),
        "duet_runs": len(duets),
        "duet_finished": sum(1 for r in duets if (r.get("duet") or {}).get("finished") is True),
        "duet_unknown": sum(1 for r in duets if (r.get("duet") or {}).get("finished") not in (True, False)),
        "per_protocol_runs": len(protocol),
        "per_protocol_hidden_pass": sum(1 for r in protocol if passed(r, "hidden")),
        "mean_cost_usd": _mean(costs), "cost_runs": len(costs),
        "mean_host_tokens": _mean(host), "mean_peer_tokens": _mean(_numbers(valid, "peer_tokens")),
        "mean_seconds": _mean(_numbers(valid, "seconds")),
        "leak_flagged": sum(1 for r in records if r.get("oracle_paths_seen")),
    }


def summarize(records: Iterable[Record]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Record]] = {}
    for record in records:
        grouped.setdefault(record["arm"], []).append(record)
    return {arm: arm_stats(grouped[arm]) for arm in sorted(grouped)}


def balance_problems(records: Sequence[Record]) -> List[str]:
    """Arms that did not run every (task, seed) the session ran: their rates cover different work."""
    cells = {(r["task"], int(r.get("seed", 1))) for r in records}
    problems: List[str] = []
    for arm in sorted({r["arm"] for r in records}):
        covered = {(r["task"], int(r.get("seed", 1))) for r in records if r["arm"] == arm}
        if covered != cells:
            problems.append(f"{arm} has {len(covered)} of {len(cells)} (task, seed) cells")
    return problems


def repeated_cells(records: Sequence[Record]) -> int:
    """How many (task, arm, seed) combinations have more than one record (all of them are counted)."""
    seen: Dict[Tuple[str, str, int], int] = {}
    for record in records:
        key = (record["task"], record["arm"], int(record.get("seed", 1)))
        seen = {**seen, key: seen.get(key, 0) + 1}
    return sum(1 for count in seen.values() if count > 1)
