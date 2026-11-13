"""The checks that decide whether a run may continue or finish."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .state import RUN_ROOT, Message, State

SKIP_DIRS = frozenset({
    ".git", RUN_ROOT, "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".venv", "venv", ".tox", "dist", "build", ".next", "coverage",
})
STALL_AFTER = 3
CHECK_TIMEOUT_SECONDS = 1800
OUTPUT_TAIL_LINES = 80
OUTPUT_TAIL_CHARS = 6000
VERDICT_RE = re.compile(r"^[\s*_`>#-]*VERDICT:\s*(APPROVE|REVISE)\b[\s*_`.]*$", re.IGNORECASE)


def parse_verdict(text: str) -> Optional[str]:
    """Return 'approve' or 'revise' from the last line that is only a verdict, else None."""
    found: Optional[str] = None
    for line in text.splitlines():
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
            elif entry.is_file() and not entry.name.endswith(".pyc"):
                found.append(entry.relative_to(workspace).as_posix())
    return found


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
            digest.update(b"x" if path.stat().st_mode & 0o111 else b"-")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            digest.update(b"<unreadable-or-deleted>")
    return digest.hexdigest()


def _tail(text: str) -> str:
    lines = text.splitlines()[-OUTPUT_TAIL_LINES:]
    return "\n".join(lines)[-OUTPUT_TAIL_CHARS:]


def run_check(state: State, timeout: int = CHECK_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Run the acceptance command the two models agreed on, inside the workspace."""
    workspace = Path(state["workspace"])
    before = fingerprint(workspace)
    try:
        done = subprocess.run(  # noqa: S602 - the acceptance command is the user's own
            state["accept_cmd"], shell=True, cwd=str(workspace),
            capture_output=True, timeout=timeout, check=False,
        )
        code: Optional[int] = done.returncode
        output = (done.stdout + done.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        code, output = None, f"acceptance command timed out after {timeout}s"
    tail = _tail(output)
    normalised = re.sub(r"\d+", "N", f"{code}\n{tail}")
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
        if message["speaker"] == speaker and message["meta"].get("verdict"):
            return message
    return None


def finish_report(state: State, messages: List[Message]) -> Tuple[bool, List[str]]:
    """Return (ready, reasons it is not ready)."""
    current = fingerprint(Path(state["workspace"]))
    blocking: List[str] = []
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
    for speaker in (state["host"], state["peer"]):
        verdict = _latest_verdict(messages, speaker)
        if verdict is None:
            blocking.append(f"{speaker} has not given a verdict")
        elif verdict["meta"]["verdict"] != "approve":
            blocking.append(f"{speaker}'s latest verdict is revise")
        elif verdict["meta"].get("fingerprint") != current:
            blocking.append(f"files changed after {speaker} approved; {speaker} must review again")
    return (not blocking, blocking)
