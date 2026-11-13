"""Turn run records into the comparison table."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Record = Dict[str, Any]
HEADERS = ("arm", "runs", "hidden pass", "visible pass", "looked done, wasn't",
           "duet finished", "mean $", "mean tokens", "mean sec")
# Claude counts cache reads and writes apart from input; Codex includes cached input in input.
TOTAL_TOKEN_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")


def load_records(root: Path) -> List[Record]:
    """Every record under a results root or a single session folder; unreadable files are named, not skipped silently."""
    paths = sorted(root.rglob("records/*.json")) if root.is_dir() else []
    records: List[Record] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path}: unreadable record ({exc})") from exc
        if isinstance(value, dict) and "arm" in value and "task" in value:
            records.append(value)
    return records


def latest_per_run(records: Iterable[Record]) -> List[Record]:
    """One record per (task, arm, seed): the most recently started, so a rerun replaces the old one."""
    chosen: Dict[Tuple[str, str, int], Record] = {}
    for record in sorted(records, key=lambda r: str(r.get("started_at", ""))):
        chosen[(record["task"], record["arm"], int(record.get("seed", 1)))] = record
    return list(chosen.values())


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _rate(hits: int, total: int) -> Optional[float]:
    return hits / total if total else None


def _passed(record: Record, which: str) -> bool:
    return bool((record.get(which) or {}).get("passed"))


def _total_tokens(record: Record) -> Optional[int]:
    tokens = record.get("tokens") or {}
    counted = [tokens[name] for name in TOTAL_TOKEN_FIELDS if isinstance(tokens.get(name), (int, float))]
    peer = record.get("peer_tokens")
    if isinstance(peer, (int, float)) and not isinstance(peer, bool):
        counted = counted + [peer]        # a duet's peer, where its status reported them
    return int(sum(counted)) if counted else None


def arm_stats(records: Sequence[Record]) -> Dict[str, Any]:
    """Aggregate one arm's records. Rates are over all its runs; means are over runs that reported the figure."""
    total = len(records)
    hidden = sum(1 for r in records if _passed(r, "hidden"))
    visible = sum(1 for r in records if _passed(r, "visible"))
    false_done = sum(1 for r in records if _passed(r, "visible") and not _passed(r, "hidden"))
    duets = [r for r in records if r.get("is_duet")]
    costs = [float(r["cost_usd"]) for r in records if isinstance(r.get("cost_usd"), (int, float))]
    tokens = [t for t in (_total_tokens(r) for r in records) if t is not None]
    return {
        "runs": total,
        "hidden_pass_rate": _rate(hidden, total),
        "visible_pass_rate": _rate(visible, total),
        "visible_pass_hidden_fail_rate": _rate(false_done, total),
        "duet_runs": len(duets),
        "duet_finished": sum(1 for r in duets if (r.get("duet") or {}).get("finished") is True),
        "duet_unknown": sum(1 for r in duets if (r.get("duet") or {}).get("finished") not in (True, False)),
        "mean_cost_usd": _mean(costs), "cost_runs": len(costs),
        "mean_tokens": _mean(tokens), "token_runs": len(tokens),
        "mean_seconds": _mean([float(r["seconds"]) for r in records if isinstance(r.get("seconds"), (int, float))]),
        "timeouts": sum(1 for r in records if r.get("timed_out")),
    }


def summarize(records: Iterable[Record]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Record]] = {}
    for record in latest_per_run(records):
        grouped.setdefault(record["arm"], []).append(record)
    return {arm: arm_stats(grouped[arm]) for arm in sorted(grouped)}


def _percent(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _row(arm: str, stats: Dict[str, Any]) -> Tuple[str, ...]:
    if stats["duet_runs"]:
        finished = f"{stats['duet_finished']}/{stats['duet_runs']}"
        if stats["duet_unknown"]:
            finished += f" ({stats['duet_unknown']} unknown)"
    else:
        finished = "-"
    cost = "-" if stats["mean_cost_usd"] is None else f"{stats['mean_cost_usd']:.2f}"
    if stats["mean_cost_usd"] is not None and stats["cost_runs"] != stats["runs"]:
        cost += f" (n={stats['cost_runs']})"
    tokens = "-" if stats["mean_tokens"] is None else f"{stats['mean_tokens']:,.0f}"
    seconds = "-" if stats["mean_seconds"] is None else f"{stats['mean_seconds']:.0f}"
    return (arm, str(stats["runs"]), _percent(stats["hidden_pass_rate"]), _percent(stats["visible_pass_rate"]),
            _percent(stats["visible_pass_hidden_fail_rate"]), finished, cost, tokens, seconds)


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]
    line = lambda cells: "  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths)).rstrip()  # noqa: E731
    return "\n".join([line(headers), line(["-" * width for width in widths])] + [line(row) for row in rows])


def format_summary(records: Sequence[Record]) -> str:
    if not records:
        return "No records found."
    stats = summarize(records)
    table = _table(HEADERS, [_row(arm, stats[arm]) for arm in stats])
    notes = [
        "",
        "hidden pass: share of runs whose finished workspace passes the hidden oracle.",
        "looked done, wasn't: share of runs that pass the visible command but fail the oracle.",
        "mean $: dollars the clients reported. Codex reports tokens only, so a Codex-hosted arm's",
        "        dollars are partial or missing; compare tokens and seconds as well.",
    ]
    return table + "\n" + "\n".join(notes)


def format_by_task(records: Sequence[Record]) -> str:
    """One row per task, one column per arm: hidden result for each seed (P pass, F fail, v = visible passed)."""
    latest = latest_per_run(records)
    arms = sorted({r["arm"] for r in latest})
    tasks = sorted({r["task"] for r in latest})
    rows = []
    for task in tasks:
        cells = [task]
        for arm in arms:
            runs = sorted((r for r in latest if r["task"] == task and r["arm"] == arm), key=lambda r: r.get("seed", 1))
            cells.append(" ".join(("P" if _passed(r, "hidden") else "F") + ("v" if _passed(r, "visible") else "")
                                  for r in runs) or "-")
        rows.append(cells)
    return _table(["task"] + arms, rows) + "\n\nP/F = hidden oracle passed/failed, v = visible command passed."
