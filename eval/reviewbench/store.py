"""What a session keeps on disk.

    <results>/<session>/session.json     one entry per invocation: settings, plan size, how it ended
    <results>/<session>/ledger.jsonl     one line per call ever started, appended as it finishes;
                                         the caps are counted from this file, so they hold across resumes
    <results>/<session>/cells/<id>.json  one record per cell: status, the reviewer's reply, its calls
    <results>/<session>/logs/<id>/       prompt, command and raw output of every call
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

MANIFEST, LEDGER, CELLS, LOGS = "session.json", "ledger.jsonl", "cells", "logs"
RECORD_VERSION = 1
OK, PARTIAL, ERROR = "ok", "partial", "error"
Record = Dict[str, Any]


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(str(temporary), str(path))


def write_record(session_dir: Path, record: Record) -> None:
    _write_atomically(session_dir / CELLS / f"{record['cell']}.json", json.dumps(record, indent=2) + "\n")


def load_records(session_dir: Path) -> Dict[str, Record]:
    """Every readable cell record, by cell id. A file that cannot be read is skipped: its cell then
    counts as not done, which costs at most a repeated call and never a wrong result."""
    records: Dict[str, Record] = {}
    for path in sorted((session_dir / CELLS).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("cell"), str):
            records[record["cell"]] = record
    return records


def append_ledger(session_dir: Path, entry: Dict[str, Any]) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    with (session_dir / LEDGER).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def load_ledger(session_dir: Path) -> List[Dict[str, Any]]:
    try:
        lines = (session_dir / LEDGER).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: List[Dict[str, Any]] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def calls_made(ledger: List[Dict[str, Any]]) -> int:
    return len(ledger)


def dollars_reported(ledger: List[Dict[str, Any]]) -> float:
    costs = (entry.get("cost_usd") for entry in ledger)
    return float(sum(cost for cost in costs if isinstance(cost, (int, float)) and not isinstance(cost, bool)))


def load_invocations(session_dir: Path) -> List[Dict[str, Any]]:
    try:
        found = json.loads((session_dir / MANIFEST).read_text(encoding="utf-8")).get("invocations")
    except (OSError, ValueError, AttributeError):
        return []
    return [item for item in found or [] if isinstance(item, dict)]


def write_manifest(session_dir: Path, earlier: List[Dict[str, Any]], invocation: Dict[str, Any]) -> None:
    _write_atomically(session_dir / MANIFEST, json.dumps({"invocations": [*earlier, invocation]}, indent=2) + "\n")


def is_session(path: Path) -> bool:
    return (path / MANIFEST).is_file() or (path / CELLS).is_dir()
