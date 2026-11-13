"""The checks that decide whether a run may continue or finish."""
from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .state import RUN_ROOT, Message, State

SKIP_DIRS = frozenset({
    ".git", RUN_ROOT, "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".venv", "venv", ".tox", "dist", "build", ".next", "coverage",
})
STALL_AFTER = 3
HASH_CHUNK_BYTES = 1 << 16
HUGE_FILE_BYTES = 100 * 1024 * 1024
CHANGE_SUMMARY_LINES = 40
VOLATILE_RE = re.compile(
    r"0x[0-9a-fA-F]+"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|(?:/private)?/(?:tmp|var/folders)/\S+"
    r"|\d+"
)
CHECK_TIMEOUT_SECONDS = 1800
OUTPUT_TAIL_LINES = 80
OUTPUT_TAIL_CHARS = 6000
VERDICT_RE = re.compile(r"^[\s*_`>#-]*VERDICT:\s*(APPROVE|REVISE)\b[\s*_`.]*$", re.IGNORECASE)


def parse_verdict(text: str) -> Optional[str]:
    """Return 'approve' or 'revise' from the last line that is only a verdict, else None."""
    found: Optional[str] = None
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = VERDICT_RE.match(line)
        if match:
            found = match.group(1).lower()
    return found


def _git_list(workspace: Path, flag: str) -> Optional[List[str]]:
    try:
        done = subprocess.run(
            ["git", "ls-files", flag, "--exclude-standard", "-z"],
            cwd=str(workspace), capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return [name for name in done.stdout.decode("utf-8", "replace").split("\0") if name]


def _is_cache(name: str) -> bool:
    return name.endswith(".pyc") or any(part in SKIP_DIRS for part in Path(name).parts)


def _git_files(workspace: Path) -> Optional[List[str]]:
    """Every tracked file, plus untracked files that are not ignored or tool caches."""
    tracked, untracked = _git_list(workspace, "-c"), _git_list(workspace, "-o")
    if tracked is None or untracked is None:
        return None
    return tracked + [name for name in untracked if not _is_cache(name)]


def _walk_files(workspace: Path) -> List[str]:
    found: List[str] = []
    stack = [workspace]
    while stack:
        folder = stack.pop()
        for entry in folder.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif (entry.is_symlink() or entry.is_file()) and not entry.name.endswith(".pyc"):
                found.append(entry.relative_to(workspace).as_posix())
    return found


def _file_digest(path: Path) -> bytes:
    if path.is_symlink():
        return b"link:" + os.readlink(path).encode("utf-8", "replace")
    info = path.stat()
    if info.st_size > HUGE_FILE_BYTES:
        return f"huge:{info.st_size}:{info.st_mtime_ns}".encode("ascii")
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.digest()


def fingerprint(workspace: Path) -> str:
    """Digest of every source file, so 'what was checked' can be compared with 'what is there now'."""
    names = _git_files(workspace)
    if names is None:
        names = _walk_files(workspace)
    digest = hashlib.sha256()
    for name in sorted(set(names)):
        if Path(name).parts[0] in (RUN_ROOT, ".git"):
            continue
        path = workspace / name
        digest.update(name.encode("utf-8", "replace") + b"\0")
        try:
            digest.update(b"x" if path.lstat().st_mode & 0o111 else b"-")
            digest.update(_file_digest(path))
        except OSError:
            digest.update(b"<unreadable-or-deleted>")
    return digest.hexdigest()


def _tail(text: str) -> str:
    lines = text.splitlines()[-OUTPUT_TAIL_LINES:]
    return "\n".join(lines)[-OUTPUT_TAIL_CHARS:]


def run_command(command: str, workspace: Path, timeout: int) -> Tuple[Optional[int], str]:
    """Run one shell command in the workspace; return (exit code or None on timeout, output tail)."""
    with tempfile.TemporaryFile() as sink:
        # Own process group and a file sink: a timeout stops everything the command started,
        # and a leftover background process cannot keep us waiting on a pipe.
        child = subprocess.Popen(  # noqa: S602 - commands here come from the user or the agreed plan
            command, shell=True, cwd=str(workspace), stdout=sink, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        try:
            code: Optional[int] = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = None
            stop_group(child)
        sink.seek(0)
        output = sink.read().decode("utf-8", "replace")
    if code is None:
        output += f"\ncommand timed out after {timeout}s"
    return code, _tail(output)


def stop_group(child: "subprocess.Popen[bytes]", grace: int = 5) -> None:
    """Ask a child's whole process group to stop, then force it."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            child.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def change_summary(workspace: Path) -> str:
    """Short list of changed files for a reviewer, when the workspace is a git repository."""
    try:
        done = subprocess.run(["git", "status", "--short"], cwd=str(workspace), capture_output=True,
                              timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if done.returncode != 0:
        return ""
    lines = [line for line in done.stdout.decode("utf-8", "replace").splitlines()
             if not line[3:].startswith(RUN_ROOT)]
    extra = len(lines) - CHANGE_SUMMARY_LINES
    shown = lines[:CHANGE_SUMMARY_LINES] + ([f"... and {extra} more"] if extra > 0 else [])
    return "\n".join(shown)


def run_check(state: State, timeout: int = CHECK_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Run the acceptance command the two models agreed on, inside the workspace."""
    workspace = Path(state["workspace"])
    before = fingerprint(workspace)
    code, tail = run_command(state["accept_cmd"], workspace, timeout)
    normalised = VOLATILE_RE.sub("N", f"{code}\n{tail}")
    after = fingerprint(workspace)
    return {
        "passed": code == 0,
        "stable": before == after,
        "exit_code": code,
        "output": tail,
        "digest": hashlib.sha256(normalised.encode("utf-8")).hexdigest(),
        "fingerprint": after,
    }


def next_streak(streak: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    if result["passed"]:
        return {"digest": None, "count": 0}
    if streak.get("digest") == result["digest"]:
        return {"digest": result["digest"], "count": streak["count"] + 1}
    return {"digest": result["digest"], "count": 1}


def is_stalled(state: State, messages: List[Message]) -> bool:
    """Three identical failures in a row, and the user has not weighed in since."""
    if state["fail_streak"]["count"] < STALL_AFTER or not state.get("last_check"):
        return False
    since = state["last_check"]["seq"]
    return not any(m["speaker"] == "user" and m["seq"] > since for m in messages)


def _latest_verdict(messages: List[Message], speaker: str) -> Optional[Message]:
    for message in reversed(messages):
        if message["speaker"] == speaker and message["phase"] == "review" and message["meta"].get("verdict"):
            return message
    return None


def both_proposed(state: State, messages: List[Message]) -> bool:
    speakers = {m["speaker"] for m in messages if m["phase"] == "propose"}
    return state["host"] in speakers and state["peer"] in speakers


def proposals_missing(state: State, messages: List[Message]) -> bool:
    return not state.get("skip_proposals") and not both_proposed(state, messages)


def current_writer(state: State, current: str) -> Optional[str]:
    """Who last changed the files. A change nothing recorded is put down to the host's side."""
    seen = state.get("seen_fingerprint")
    if seen is not None and seen != current:
        return state["host"]
    return state.get("last_writer")


def finish_report(state: State, messages: List[Message]) -> Tuple[bool, List[str]]:
    """Return (ready, reasons it is not ready)."""
    current = fingerprint(Path(state["workspace"]))
    blocking: List[str] = []
    if proposals_missing(state, messages):
        blocking.append("the blind proposal round was skipped: both models must record a proposal")
    if not state.get("accept_waived"):
        check = state.get("last_check")
        if not state.get("accept_cmd"):
            blocking.append("no acceptance command is set")
        elif not check:
            blocking.append("the acceptance command has never been run")
        elif not check["passed"]:
            blocking.append("the last acceptance run failed")
        elif not check.get("stable", True):
            blocking.append("files changed while the acceptance command was running; run check again")
        elif check["fingerprint"] != current:
            blocking.append("files changed after the last passing acceptance run; run check again")
    writer = current_writer(state, current)
    reviewers = [s for s in (state["host"], state["peer"]) if s != writer]
    for speaker in reviewers:
        verdict = _latest_verdict(messages, speaker)
        if verdict is None:
            blocking.append(f"{speaker} has not given a verdict in the review phase")
        elif verdict["meta"]["verdict"] != "approve":
            blocking.append(f"{speaker}'s latest review verdict is revise")
        elif verdict["meta"].get("fingerprint") != current:
            blocking.append(f"files changed after {speaker} approved; {speaker} must review again")
    return (not blocking, blocking)
