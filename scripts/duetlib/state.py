"""Run folder layout: state.json, messages.jsonl (source of truth), conversation.md (readable copy)."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

RUN_ROOT = ".duet"
STATE_FILE = "state.json"
MESSAGES_FILE = "messages.jsonl"
TRANSCRIPT_FILE = "conversation.md"
HOST_FOR_PEER = {"codex": "claude", "claude": "codex"}
PHASES = ("frame", "propose", "debate", "plan", "build", "test", "review")
DEFAULT_MAX_TURNS = 30

State = Dict[str, Any]
Message = Dict[str, Any]


class DuetError(Exception):
    """A problem the caller should see as a plain message."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_state(run: Path) -> State:
    path = run / STATE_FILE
    if not path.is_file():
        raise DuetError(f"not a duet run folder (no {STATE_FILE}): {run}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DuetError(f"{path} is not valid JSON: {exc}") from exc


def save_state(run: Path, state: State) -> None:
    _write_atomic(run / STATE_FILE, json.dumps(state, indent=2, sort_keys=True) + "\n")


def updated(state: State, **changes: Any) -> State:
    """Return a copy of the state with the given fields replaced."""
    return {**state, **changes}


def read_messages(run: Path) -> List[Message]:
    path = run / MESSAGES_FILE
    if not path.is_file():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        return [json.loads(line) for line in lines]
    except json.JSONDecodeError as exc:
        raise DuetError(f"{path} has a damaged line: {exc}") from exc


def _render(message: Message) -> str:
    label = message["speaker"].upper()
    verdict = (message.get("meta") or {}).get("verdict")
    suffix = f" · verdict: {verdict}" if verdict else ""
    head = f"## {message['seq']}. {label} · {message['phase']} · {message['ts']}{suffix}"
    return f"{head}\n\n{message['text'].rstrip()}\n\n"


def append_message(
    run: Path,
    speaker: str,
    phase: str,
    text: str,
    kind: str = "message",
    meta: Optional[Dict[str, Any]] = None,
) -> Message:
    message: Message = {
        "seq": len(read_messages(run)) + 1,
        "ts": now_iso(),
        "speaker": speaker,
        "phase": phase,
        "kind": kind,
        "text": text,
        "meta": dict(meta or {}),
    }
    with (run / MESSAGES_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(message, ensure_ascii=False) + "\n")
    with (run / TRANSCRIPT_FILE).open("a", encoding="utf-8") as fh:
        fh.write(_render(message))
    return message


def new_run(
    workspace: Path,
    goal: str,
    peer: str,
    accept_cmd: Optional[str],
    accept_waived: Optional[str],
    max_turns: int,
    peer_model: Optional[str],
    peer_effort: Optional[str] = None,
) -> Path:
    if peer not in HOST_FOR_PEER:
        raise DuetError(f"unknown peer '{peer}'; choose one of {sorted(HOST_FOR_PEER)}")
    if not workspace.is_dir():
        raise DuetError(f"workspace does not exist: {workspace}")
    if peer_effort and not peer_effort.isalnum():
        raise DuetError("--peer-effort must be a single word such as low, high or ultra")
    if not goal.strip():
        raise DuetError("the goal is empty")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    run = workspace.resolve() / RUN_ROOT / run_id
    (run / "turns").mkdir(parents=True)
    state: State = {
        "run_id": run_id,
        "status": "active",
        "created": now_iso(),
        "workspace": str(workspace.resolve()),
        "peer": peer,
        "host": HOST_FOR_PEER[peer],
        "peer_model": peer_model,
        "peer_effort": peer_effort,
        "peer_session": None,
        "peer_cursor": 0,
        "withheld": [],
        "peer_turns": 0,
        "max_turns": max_turns,
        "accept_cmd": accept_cmd,
        "accept_waived": accept_waived,
        "last_check": None,
        "fail_streak": {"digest": None, "count": 0},
    }
    save_state(run, state)
    (run / TRANSCRIPT_FILE).write_text(
        f"# Duet {run_id}: {state['host']} and {peer}\n\n", encoding="utf-8"
    )
    append_message(run, "user", "frame", goal.strip(), kind="goal")
    return run
