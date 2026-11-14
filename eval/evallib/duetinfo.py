"""Did a duet actually take place in this workspace, and did it finish? Read from what the duet
runner left behind, without importing the runner.

Three sources, most trustworthy first:
  1. the runner's state directory (`$DUET_HOME/runs/<id>/state.json`), matched to this run by the
     state's own "workspace" field;
  2. a run kept inside the workspace (`.duet/`), which the runner falls back to when it cannot
     write to its state directory;
  3. a status report in the host's final message (the prompt asks for one).
When none is found the outcome is "unknown", never a guess."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple, Union

FINISHED_WORDS = ("done", "finished", "complete", "completed")
RUN_FOLDER = ".duet"
RUNS_FOLDER = "runs"
STATE_FILE = "state.json"
MAX_JSON_BYTES = 2_000_000
UNKNOWN = "unknown"
FOUND_STATE_DIR, FOUND_WORKSPACE, FOUND_MESSAGE = "state-dir", "workspace", "final-message"
Finished = Union[bool, str]
Report = Dict[str, Any]


def json_objects(text: str) -> Iterator[Report]:
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


def _read_object(path: Path) -> Optional[Report]:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _newest_first(paths: Iterator[Path]) -> Iterator[Path]:
    def modified(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0
    return iter(sorted(paths, key=modified, reverse=True))


def _is_report(value: Optional[Report]) -> bool:
    return value is not None and isinstance(value.get("status"), str)


def _in_state_dir(workspace: Path, duet_home: Optional[Path]) -> Optional[Tuple[Report, str]]:
    """The newest run in the runner's state directory whose state names this workspace."""
    runs = duet_home / RUNS_FOLDER if duet_home is not None else None
    if runs is None or not runs.is_dir():
        return None
    wanted = workspace.resolve()
    for path in _newest_first(runs.glob(f"*/{STATE_FILE}")):
        state = _read_object(path)
        named = state.get("workspace") if state is not None else None
        if _is_report(state) and isinstance(named, str) and Path(named).resolve() == wanted:
            return state, str(path.parent)
    return None


def _in_workspace(workspace: Path) -> Optional[Tuple[Report, str]]:
    folder = workspace / RUN_FOLDER
    if not folder.is_dir():
        return None
    for path in _newest_first(folder.rglob("*.json")):
        state = _read_object(path)
        if _is_report(state):
            return state, str(path.parent)
    return None


def _in_message(final_message: str) -> Optional[Tuple[Report, Optional[str]]]:
    report = next((value for value in json_objects(final_message) if _is_report(value)), None)
    if report is None:
        return None
    return report, report["run"] if isinstance(report.get("run"), str) else None


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _peer_figures(report: Report) -> Dict[str, Any]:
    """Peer turns, dollars and tokens as the runner counted them. Dollars are kept only when the
    peer's client reported some: a Codex peer reports none and the runner stores that as 0."""
    usage = report.get("peer_usage") if isinstance(report.get("peer_usage"), dict) else {}
    turns, cost = _number(report.get("peer_turns")), _number(usage.get("cost_usd"))
    tokens = [count for count in (_number(usage.get("input_tokens")), _number(usage.get("output_tokens")))
              if count is not None]
    return {"peer_turns": int(turns) if turns is not None else None,
            "peer_cost_usd": cost if cost else None,
            "peer_tokens": int(sum(tokens)) if tokens else None}


def duet_outcome(workspace: Path, final_message: str, duet_home: Optional[Path] = None) -> Dict[str, Any]:
    """{"found", "ran", "finished", "per_protocol", "status", "run_folder", "peer_turns",
    "peer_cost_usd", "peer_tokens"}.

    `ran` is True once the peer has taken a turn, False when a run exists with no peer turn or no
    run exists at all, and "unknown" when the only evidence does not say. `per_protocol` is True
    only for a duet that both ran and finished: the runs that tested the duet as designed."""
    located = _in_state_dir(workspace, duet_home)
    source = FOUND_STATE_DIR
    if located is None:
        located, source = _in_workspace(workspace), FOUND_WORKSPACE
    if located is None:
        located, source = _in_message(final_message), FOUND_MESSAGE
    if located is None:
        return {"found": None, "ran": False, "finished": UNKNOWN, "per_protocol": False, "status": None,
                "run_folder": None, "peer_turns": None, "peer_cost_usd": None, "peer_tokens": None}
    report, run_folder = located
    figures = _peer_figures(report)
    finished = report["status"].lower() in FINISHED_WORDS
    ran: Finished = UNKNOWN if figures["peer_turns"] is None else figures["peer_turns"] > 0
    return {"found": source, "ran": ran, "finished": finished, "per_protocol": finished and ran is True,
            "status": report["status"], "run_folder": run_folder, **figures}
