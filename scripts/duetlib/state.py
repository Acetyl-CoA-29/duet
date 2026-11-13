"""Run folder layout: state.json, messages.jsonl (source of truth), conversation.md (readable copy)."""
from __future__ import annotations

import base64
import fcntl
import json
import os
import secrets
import tempfile
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

RUN_ROOT = ".duet"
STATE_FILE = "state.json"
MESSAGES_FILE = "messages.jsonl"
TRANSCRIPT_FILE = "conversation.md"
HOST_FOR_PEER = {"codex": "claude", "claude": "codex"}
PHASES = ("frame", "propose", "debate", "plan", "build", "test", "review")
DEFAULT_MAX_TURNS = 30
SEALED_NOTE = "(sealed blind proposal; shown here once the peer has recorded its own)"

State = Dict[str, Any]
Message = Dict[str, Any]


class DuetError(Exception):
    """A problem the caller should see as a plain message."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, text: str) -> None:
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


@contextmanager
def locked(run: Path) -> Iterator[None]:
    """Hold the run's lock so two commands cannot number a message or write state at once."""
    with (run / ".lock").open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _seal(text: str) -> str:
    """Encode a proposal so a file search in the workspace cannot surface it before the reveal."""
    return base64.b64encode(zlib.compress(text.encode("utf-8"))).decode("ascii")


def _unseal(blob: str) -> str:
    return zlib.decompress(base64.b64decode(blob)).decode("utf-8")


def load_state(run: Path) -> State:
    path = run / STATE_FILE
    if not path.is_file():
        raise DuetError(f"not a duet run folder (no {STATE_FILE}): {run}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DuetError(f"{path} is not valid JSON: {exc}") from exc


def save_state(run: Path, state: State) -> None:
    with locked(run):
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
        raw = [json.loads(line) for line in lines]
    except json.JSONDecodeError as exc:
        raise DuetError(f"{path} has a damaged line: {exc}") from exc
    return [{**m, "text": _unseal(m["sealed_text"])} if m.get("sealed_text") else m for m in raw]


def _render(message: Message) -> str:
    label = message["speaker"].upper()
    verdict = (message.get("meta") or {}).get("verdict")
    suffix = f" · verdict: {verdict}" if verdict else ""
    head = f"## {message['seq']}. {label} · {message['phase']} · {message['ts']}{suffix}"
    body = SEALED_NOTE if message.get("sealed_text") else message["text"].rstrip()
    return f"{head}\n\n{body}\n\n"


def append_message(
    run: Path,
    speaker: str,
    phase: str,
    text: str,
    kind: str = "message",
    meta: Optional[Dict[str, Any]] = None,
    sealed: bool = False,
) -> Message:
    with locked(run):
        message: Message = {
            "seq": len(read_messages(run)) + 1,
            "ts": now_iso(),
            "speaker": speaker,
            "phase": phase,
            "kind": kind,
            "text": "" if sealed else text,
            "meta": dict(meta or {}),
        }
        if sealed:
            message["sealed_text"] = _seal(text)
        with (run / MESSAGES_FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(message, ensure_ascii=False) + "\n")
        with (run / TRANSCRIPT_FILE).open("a", encoding="utf-8") as fh:
            fh.write(_render(message))
    return {**message, "text": text}


def reveal_sealed(run: Path, speaker: str) -> None:
    """Write the speaker's sealed proposals into the readable transcript, once the blind round is over."""
    sealed = [m for m in read_messages(run) if m.get("sealed_text") and m["speaker"] == speaker]
    with locked(run):
        with (run / TRANSCRIPT_FILE).open("a", encoding="utf-8") as fh:
            for message in sealed:
                fh.write(f"## Revealed: message {message['seq']} from {speaker.upper()} · propose\n\n"
                         f"{message['text'].rstrip()}\n\n")


def _git_exclude(workspace: Path) -> None:
    """Keep transcripts out of commits: list the run folder in the repository's local exclude file."""
    info = workspace / ".git" / "info"
    if not info.is_dir():
        return
    exclude = info / "exclude"
    current = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if f"{RUN_ROOT}/" not in current.splitlines():
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write(("" if current.endswith("\n") or not current else "\n") + f"{RUN_ROOT}/\n")


def new_run(
    workspace: Path,
    goal: str,
    peer: str,
    accept_cmd: Optional[str],
    accept_waived: Optional[str],
    max_turns: int,
    peer_model: Optional[str],
    peer_effort: Optional[str] = None,
    peer_user_config: bool = False,
    max_cost_usd: Optional[float] = None,
    skip_proposals: Optional[str] = None,
    seen_fingerprint: Optional[str] = None,
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
    (run / "drafts").mkdir()
    (run / "checks").mkdir()
    _git_exclude(workspace.resolve())
    state: State = {
        "run_id": run_id,
        "status": "active",
        "created": now_iso(),
        "workspace": str(workspace.resolve()),
        "peer": peer,
        "host": HOST_FOR_PEER[peer],
        "peer_model": peer_model,
        "peer_effort": peer_effort,
        "peer_user_config": peer_user_config,
        "peer_session": None,
        "peer_cursor": 0,
        "withheld": [],
        "peer_turns": 0,
        "peer_usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        "max_cost_usd": max_cost_usd,
        "skip_proposals": skip_proposals,
        "nonce": secrets.token_hex(4),
        "seen_fingerprint": seen_fingerprint,
        "init_fingerprint": seen_fingerprint,
        "peer_primed": False,
        "last_writer": None,
        "last_peer_failed": False,
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
