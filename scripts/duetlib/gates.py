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
    r"|\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds|min)\b"
)
CHECK_TIMEOUT_SECONDS = 1800
OUTPUT_TAIL_LINES = 80
OUTPUT_HEAD_LINES = 25
OUTPUT_TAIL_CHARS = 6000
VERDICT_RE = re.compile(r"^[\s*_`#-]*VERDICT:\s*(APPROVE|REVISE)\b[\s*_`.]*$", re.IGNORECASE)


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


def manifest(workspace: Path) -> Dict[str, str]:
    """Digest of every source file by path, so a change can be named as well as detected."""
    names = _git_files(workspace)
    if names is None:
        names = _walk_files(workspace)
    found: Dict[str, str] = {}
    for name in sorted(set(names)):
        if Path(name).parts[0] in (RUN_ROOT, ".git"):
            continue
        path = workspace / name
        try:
            mode = b"x" if path.lstat().st_mode & 0o111 else b"-"
            found[name] = hashlib.sha256(mode + _file_digest(path)).hexdigest()
        except OSError:
            found[name] = "unreadable-or-deleted"
    return found


def digest_of(files: Dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8", "replace") + b"\0" + files[name].encode("ascii") + b"\n")
    return digest.hexdigest()


def fingerprint(workspace: Path) -> str:
    """One digest for the whole workspace: 'what was checked' against 'what is there now'."""
    return digest_of(manifest(workspace))


def changed_files(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))


def excerpt(output: str) -> str:
    """The start and the end of long output: the first error and the final summary both matter."""
    lines = output.splitlines()
    if len(lines) > OUTPUT_HEAD_LINES + OUTPUT_TAIL_LINES:
        omitted = len(lines) - OUTPUT_HEAD_LINES - OUTPUT_TAIL_LINES
        lines = lines[:OUTPUT_HEAD_LINES] + [f"... {omitted} lines omitted ..."] + lines[-OUTPUT_TAIL_LINES:]
    text = "\n".join(lines)
    return text if len(text) <= OUTPUT_TAIL_CHARS else text[:1500] + "\n... cut ...\n" + text[-(OUTPUT_TAIL_CHARS - 1500):]


def _tail(text: str) -> str:
    lines = text.splitlines()[-OUTPUT_TAIL_LINES:]
    return "\n".join(lines)[-OUTPUT_TAIL_CHARS:]


def run_command(command: str, workspace: Path, timeout: int) -> Tuple[Optional[int], str]:
    """Run one shell command in the workspace; return (exit code or None on timeout, full output)."""
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
    return code, output


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
    before = manifest(workspace)
    code, output = run_command(state["accept_cmd"], workspace, timeout)
    after = manifest(workspace)
    normalised = VOLATILE_RE.sub("N", f"{code}\n{_tail(output)}")
    return {
        "passed": code == 0,
        "stable": before == after,
        "changed_during": changed_files(before, after),
        "exit_code": code,
        "output": excerpt(output),
        "full_output": output,
        "digest": hashlib.sha256(normalised.encode("utf-8")).hexdigest(),
        "fingerprint": digest_of(after),
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


def review_standing(messages: List[Message], speaker: str, after_seq: int) -> Tuple[Optional[Message], str]:
    """The speaker's latest review-phase message and why it does not count as an approval ('' if it does)."""
    latest = next((m for m in reversed(messages)
                   if m["speaker"] == speaker and m["phase"] == "review" and m["kind"] == "message"), None)
    if latest is None or not (latest["meta"].get("verdict") or latest["meta"].get("verdict_ignored")):
        said_more = latest is not None
        return latest, (f"{speaker}'s latest review message carries no verdict" if said_more
                        else f"{speaker} has not given a verdict in the review phase")
    if latest["meta"].get("verdict") != "approve":
        return latest, f"{speaker}'s latest review verdict is not an approval that counts"
    if latest["seq"] < after_seq:
        return latest, f"{speaker} approved before the last acceptance run; {speaker} must review again"
    return latest, ""


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
    after_seq = 0 if state.get("accept_waived") else (state.get("last_check") or {}).get("seq", 0)
    for speaker in reviewers:
        verdict, problem = review_standing(messages, speaker, after_seq)
        if problem:
            blocking.append(problem)
        elif verdict is not None and verdict["meta"].get("fingerprint") != current:
            blocking.append(f"files changed after {speaker} approved; {speaker} must review again")
    return (not blocking, blocking)
