"""Best-effort reading of how a duet ended, without depending on the runner's internals.

Two sources, in order: a status report printed in the host's final message (the prompt asks for
one), then any JSON file in the workspace's `.duet/` folder that carries a "status" field. If
neither is found the outcome is "unknown", never a guess."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

FINISHED_WORDS = ("done", "finished", "complete", "completed")
RUN_FOLDER = ".duet"
MAX_JSON_BYTES = 2_000_000
UNKNOWN = "unknown"
Finished = Union[bool, str]


def json_objects(text: str) -> Iterator[Dict[str, Any]]:
    """Every top-level JSON object embedded in free text."""
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict):
            yield value
        index = text.find("{", end)


def _candidates(workspace: Path, final_message: str) -> Iterator[Dict[str, Any]]:
    yield from json_objects(final_message)
    folder = workspace / RUN_FOLDER
    if not folder.is_dir():
        return
    for path in sorted(folder.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            if path.stat().st_size > MAX_JSON_BYTES:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            yield value


def _numbers(value: Any, wanted: str, skip: str = "max") -> List[float]:
    """Numeric values anywhere in a structure whose key contains `wanted` (and not `skip`)."""
    found: List[float] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            name = str(key).lower()
            if wanted in name and skip not in name and isinstance(inner, (int, float)) and not isinstance(inner, bool):
                found.append(float(inner))
            else:
                found.extend(_numbers(inner, wanted, skip))
    elif isinstance(value, list):
        for inner in value:
            found.extend(_numbers(inner, wanted, skip))
    return found


def duet_outcome(workspace: Path, final_message: str) -> Dict[str, Any]:
    """{"finished": True | False | "unknown", "status", "started", "run_folder", "peer_turns",
    "peer_cost_usd", "peer_tokens"}. A peer cost of 0 means the peer's client reported no dollars."""
    started = (workspace / RUN_FOLDER).is_dir() and any((workspace / RUN_FOLDER).iterdir())
    report: Optional[Dict[str, Any]] = next(
        (c for c in _candidates(workspace, final_message) if isinstance(c.get("status"), str)), None)
    if report is None:
        return {"finished": UNKNOWN, "status": None, "started": started, "run_folder": None,
                "peer_turns": None, "peer_cost_usd": None, "peer_tokens": None}
    costs = _numbers(report, "cost")
    turns = report.get("peer_turns")
    tokens = _numbers(report.get("peer_usage"), "tokens")
    return {
        "finished": report["status"].lower() in FINISHED_WORDS,
        "status": report["status"],
        "started": True if started else UNKNOWN,
        "run_folder": report["run"] if isinstance(report.get("run"), str) else None,
        "peer_turns": turns if isinstance(turns, int) and not isinstance(turns, bool) else None,
        "peer_cost_usd": max(costs) if costs else None,
        "peer_tokens": int(sum(tokens)) if tokens else None,
    }
