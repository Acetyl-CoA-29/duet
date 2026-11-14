"""Print the comparison: one table of results and one of spend, with counts beside every rate and
the warnings a reader needs before believing a difference."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import summary

Record = summary.Record
RESULT_HEADERS = ("arm", "runs", "infra", "timeouts", "hidden pass", "95% interval", "visible pass",
                  "looked done, wasn't", "duet finished", "finished-duet hidden pass")
COST_HEADERS = ("arm", "mean $ reported", "host tokens", "peer tokens", "mean sec")
EXPLANATION = (
    "runs: every run recorded. infra: runs where the tooling failed (client missing, no duet took",
    "  place, permission or quota errors); they are left out of every rate. timeouts are counted",
    "  as runs and scored on whatever was in the workspace.",
    "hidden pass: runs whose finished workspace passes the hidden oracle, with a 95% Wilson interval.",
    "  Differences between arms whose intervals overlap are not evidence of a difference.",
    "looked done, wasn't: runs that pass the visible command but fail the oracle.",
    "finished-duet hidden pass: only duets in which the peer took a turn and the run finished.",
)
COST_EXPLANATION = (
    "mean $ reported: dollars a client itself reported. Codex reports none, so a Codex-hosted arm",
    "  shows its Claude peer only and solo-codex shows nothing. Not a like-for-like price.",
    "host tokens: all input (cached included) plus output of the client the harness started.",
    "peer tokens: as the duet runner counted them: a Codex peer's include cached input, a Claude",
    "  peer's do not. The two columns measure different things and are never added together.",
)


def _share(hits: int, total: int) -> str:
    return "-" if total == 0 else f"{hits}/{total} ({hits / total * 100:.0f}%)"


def _interval(bounds: Optional[Tuple[float, float]]) -> str:
    return "-" if bounds is None else f"{bounds[0] * 100:.0f}-{bounds[1] * 100:.0f}%"


def _number(value: Optional[float], pattern: str) -> str:
    return "-" if value is None else format(value, pattern)


def _result_row(arm: str, stats: Dict[str, Any]) -> Tuple[str, ...]:
    finished, protocol = "-", "-"
    if stats["duet_runs"]:
        finished = f"{stats['duet_finished']}/{stats['duet_runs']}"
        if stats["duet_unknown"]:
            finished += f" ({stats['duet_unknown']} unknown)"
        protocol = _share(stats["per_protocol_hidden_pass"], stats["per_protocol_runs"])
    valid = stats["valid"]
    return (arm, str(stats["runs"]), str(stats["infra"]), str(stats["timeouts"]),
            _share(stats["hidden_pass"], valid), _interval(stats["hidden_ci"]),
            _share(stats["visible_pass"], valid), _share(stats["false_done"], valid), finished, protocol)


def _cost_row(arm: str, stats: Dict[str, Any]) -> Tuple[str, ...]:
    cost = _number(stats["mean_cost_usd"], ".2f")
    if stats["mean_cost_usd"] is not None and stats["cost_runs"] != stats["valid"]:
        cost += f" (n={stats['cost_runs']})"
    return (arm, cost, _number(stats["mean_host_tokens"], ",.0f"), _number(stats["mean_peer_tokens"], ",.0f"),
            _number(stats["mean_seconds"], ".0f"))


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * width for width in widths])] + [line(row) for row in rows])


def warnings(records: Sequence[Record], stats: Dict[str, Dict[str, Any]]) -> List[str]:
    """What a reader must know before comparing the rows."""
    found = [f"UNBALANCED: {problem}; rates for different arms cover different work."
             for problem in summary.balance_problems(records)]
    flagged = sum(arm["leak_flagged"] for arm in stats.values())
    if flagged:
        found.append(f"{flagged} run(s) named the oracle or a protected folder in their output "
                     "(oracle_paths_seen in the record): read them before counting them.")
    return found


def format_summary(records: Sequence[Record], title: Optional[str] = None, notes: Sequence[str] = (),
                   legend: bool = True) -> str:
    """The two tables for one set of records, with their warnings; `notes` are added by the caller.
    `legend` adds what each column means."""
    if not records:
        return "No records found."
    stats = summary.summarize(records)
    parts = [title] if title else []
    parts.append(_table(RESULT_HEADERS, [_result_row(arm, stats[arm]) for arm in stats]))
    parts.append("")
    parts.append(_table(COST_HEADERS, [_cost_row(arm, stats[arm]) for arm in stats]))
    parts.append("")
    parts.extend([*warnings(records, stats), *notes])
    if legend:
        parts.extend([*EXPLANATION, *COST_EXPLANATION])
    return "\n".join(parts).rstrip()


def _mark(record: Record) -> str:
    if summary.is_infra(record):
        return "I"
    return ("P" if summary.passed(record, "hidden") else "F") + ("v" if summary.passed(record, "visible") else "")


def format_by_task(records: Sequence[Record]) -> str:
    """One row per task, one column per arm, one mark per recorded run in seed order."""
    arms = sorted({r["arm"] for r in records})
    rows = []
    for task in sorted({r["task"] for r in records}):
        cells = [task]
        for arm in arms:
            runs = sorted((r for r in records if r["task"] == task and r["arm"] == arm),
                          key=lambda r: (r.get("seed", 1), str(r.get("started_at", ""))))
            cells.append(" ".join(_mark(r) for r in runs) or "-")
        rows.append(cells)
    return (_table(["task"] + arms, rows)
            + "\n\nP/F = hidden oracle passed/failed, v = visible command passed, I = infrastructure failure.")
