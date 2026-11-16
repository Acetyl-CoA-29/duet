"""The checks that decide whether a run may continue or finish."""
from __future__ import annotations

import hashlib
import os
import re
import signal
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .state import RUN_ROOT, DuetError, Message, State

# Folders that tools fill as a side effect of running code and nobody writes by hand. They are left out
# of the fingerprint wherever they sit. Anything else (build, dist, coverage, venv ...) counts as source
# unless git ignores it.
CACHE_DIRS = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "node_modules", ".venv",
    ".cc-writes",  # the Claude client's sandbox leaves this (empty) under .claude/ on every turn
})
SKIP_DIRS = CACHE_DIRS | {".git"}
EXCLUDE_FILE = ".git/info/exclude"  # fingerprinted so a turn cannot hide files by listing them there
MISSING = "unreadable-or-deleted"
DIR_SUFFIX = "/"  # directories are listed under their name with this suffix
GIT_TIMEOUT_SECONDS = 60
STALL_AFTER = 3
HASH_CHUNK_BYTES = 1 << 16
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
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def parse_verdict(text: str) -> Optional[str]:
    """Return 'approve' or 'revise' when the reply's last non-empty line is only a verdict, outside any
    code fence; else None. A verdict followed by more text, or quoted inside a fence, does not count."""
    fence: Optional[Tuple[str, int]] = None  # the open fence's character and length
    last: Optional[Tuple[str, bool]] = None  # the last non-empty line and whether it is fence material
    for line in text.splitlines():
        mark = FENCE_RE.match(line)
        if fence is None:
            if mark and not (mark.group(1)[0] == "`" and "`" in mark.group(2)):
                fence = (mark.group(1)[0], len(mark.group(1)))
                last = (line, True)
            elif line.strip():
                last = (line, False)
            continue
        closes = (mark is not None and mark.group(1)[0] == fence[0] and len(mark.group(1)) >= fence[1]
                  and not mark.group(2).strip())
        if closes:
            fence = None
        if line.strip():
            last = (line, True)
    if last is None or last[1]:
        return None
    match = VERDICT_RE.match(last[0])
    return match.group(1).lower() if match else None


def _git(folder: Path, *args: str) -> Optional[bytes]:
    """Output of one git command run in the folder, or None when git is missing or refuses."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    try:
        done = subprocess.run(["git", *args], cwd=str(folder), capture_output=True, env=env,
                              timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _is_repo_root(folder: Path) -> bool:
    """True only when the folder is itself the top of a git work tree. A folder that merely sits inside
    someone else's repository (possibly an ignored part of it) is walked instead."""
    out = _git(folder, "rev-parse", "--show-toplevel")
    if out is None:
        return False
    try:
        return os.path.samefile(os.fsdecode(out).rstrip("\n"), str(folder))
    except OSError:
        return False


def _git_list(folder: Path, *flags: str) -> Optional[List[str]]:
    out = _git(folder, "ls-files", *flags, "--exclude-standard", "-z")
    if out is None:
        return None
    return [os.fsdecode(name) for name in out.split(b"\0") if name]


def _is_cache(name: str) -> bool:
    return name.endswith(".pyc") or any(part in CACHE_DIRS for part in Path(name).parts)


def _real_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:  # a folder on the way cannot be searched: nothing can be said about what is behind it
        return False


def _listing(folder: str) -> Optional[List["os.DirEntry[str]"]]:
    """The entries of one folder, or None when it cannot be read (no permission, or gone meanwhile)."""
    try:
        with os.scandir(folder) as entries:
            return list(entries)
    except OSError:
        return None


def _entry_is_dir(entry: "os.DirEntry[str]") -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _entry_is_file(entry: "os.DirEntry[str]") -> bool:
    try:
        return entry.is_symlink() or entry.is_file(follow_symlinks=False)
    except OSError:
        return False


def _walk(root: Path, depth: int, pruned: frozenset = frozenset(), files: bool = True) -> List[str]:
    """Files, links and empty folders under root, skipping tool caches and .git. A nested repository is
    listed by its own rules. With files=False only empty folders are returned (git lists the files).

    A folder that cannot be read is listed as itself (name plus slash), so its changed permissions show
    up as a change instead of stopping the scan."""
    found: List[str] = []
    base = str(root)
    stack = [""]
    while stack:
        here = stack.pop()
        entries = _listing(os.path.join(base, here) if here else base)
        if entries is None:
            if not here and depth == 0:
                raise DuetError(f"the workspace folder cannot be read: {base}. Give yourself access again "
                                f"(`chmod u+rwx {base}`) and repeat the command")
            found.append(here + DIR_SUFFIX if here else "")  # "" names a nested repository's own folder
            continue
        if not entries and here:
            found.append(here + DIR_SUFFIX)
        for entry in entries:
            rel = f"{here}/{entry.name}" if here else entry.name
            if _entry_is_dir(entry):
                if entry.name in SKIP_DIRS or rel in pruned or (not here and depth == 0 and entry.name == RUN_ROOT):
                    continue
                if os.path.lexists(os.path.join(entry.path, ".git")):
                    if files:
                        found += [f"{rel}/{inner}" for inner in _names(Path(entry.path), depth + 1)]
                else:
                    stack.append(rel)
            elif files and _entry_is_file(entry) and not entry.name.endswith(".pyc"):
                found.append(rel)
    return found


def _git_names(root: Path, depth: int) -> Optional[List[str]]:
    """Every tracked file, every untracked file git does not ignore (tool caches aside), the contents of
    nested repositories and submodules, and folders outside ignored ones."""
    tracked, untracked = _git_list(root, "-c"), _git_list(root, "-o")
    ignored = _git_list(root, "-o", "-i", "--directory")
    if tracked is None or untracked is None or ignored is None:
        return None
    names: List[str] = []
    for name in tracked + [name for name in untracked if not _is_cache(name)]:
        if name.endswith("/") or _real_dir(root / name):  # a nested repository, or a file turned folder
            base = name.rstrip("/")
            names += [f"{base}/{inner}" for inner in _names(root / base, depth + 1)]
        else:
            names.append(name)
    pruned = frozenset(name.rstrip("/") for name in ignored if name.endswith("/"))
    return names + _walk(root, depth, pruned, files=False)


def _names(root: Path, depth: int = 0) -> List[str]:
    listed = _git_names(root, depth) if _is_repo_root(root) else None
    return listed if listed is not None else _walk(root, depth)


def _link_above(workspace: Path, name: str, known: Dict[str, bool]) -> bool:
    """True when a folder on the way to the name is a symbolic link: the file is then not in the workspace."""
    parent = os.path.dirname(name.rstrip("/"))
    if not parent:
        return False
    if parent not in known:
        # os.path.islink answers False, without raising, when a folder on the way cannot be searched.
        known[parent] = os.path.islink(os.path.join(str(workspace), parent)) or _link_above(workspace, parent, known)
    return known[parent]


def _hash_file(path: Path, skip_line: Optional[bytes] = None) -> bytes:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        if skip_line is not None:
            hasher.update(b"\n".join(line for line in fh.read().splitlines() if line.strip() != skip_line))
            return hasher.digest()
        for chunk in iter(lambda: fh.read(HASH_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.digest()


def _entry_digest(path: Path, name: str) -> str:
    """Digest of one file: its kind, all its permission bits and its content (a link: its target)."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return hashlib.sha256(b"link:" + os.fsencode(os.readlink(path))).hexdigest()
    mode = f"{stat.S_IMODE(info.st_mode):04o}".encode("ascii")
    if stat.S_ISDIR(info.st_mode):
        return hashlib.sha256(b"dir:" + mode).hexdigest()
    if not stat.S_ISREG(info.st_mode):
        return hashlib.sha256(f"special:{stat.S_IFMT(info.st_mode)}".encode("ascii")).hexdigest()
    if name == EXCLUDE_FILE:  # the runner's own `.duet/` line there is not a change
        return hashlib.sha256(_hash_file(path, skip_line=RUN_ROOT.encode("ascii") + b"/")).hexdigest()
    return hashlib.sha256(mode + _hash_file(path)).hexdigest()


def _ancestors(names: List[str]) -> List[str]:
    """Every folder on the way to a listed name, as relative paths without the trailing slash."""
    seen: set = set()
    for name in names:
        parent = os.path.dirname(name.rstrip("/"))
        while parent and parent not in seen:
            seen.add(parent)
            parent = os.path.dirname(parent)
    return sorted(seen)


def _digest_or_missing(workspace: Path, name: str) -> str:
    try:
        return _entry_digest(workspace / name, name)
    except OSError:
        return MISSING


def manifest(workspace: Path) -> Dict[str, str]:
    """Digest of every source file by path, so a change can be named as well as detected. Folders
    are listed with a trailing slash; a file reached only through a symbolic link counts as missing,
    and so does anything behind a folder that cannot be searched (the folder itself is listed)."""
    names = [name for name in _names(workspace) if name]
    # Cover source ancestors (even when git ignores them), without adding folders that contain only
    # caches or ignored files. A replaced ancestor is its own entry, never a directory alias.
    names += [parent + (DIR_SUFFIX if _real_dir(workspace / parent) else "") for parent in _ancestors(names)]
    if (workspace / EXCLUDE_FILE).is_file() and not (workspace / ".git").is_symlink():
        names.append(EXCLUDE_FILE)
    found: Dict[str, str] = {}
    links: Dict[str, bool] = {}
    for name in sorted(set(names)):
        top = name.split("/", 1)[0]
        if top == RUN_ROOT or (top == ".git" and name != EXCLUDE_FILE):
            continue
        found[name] = MISSING if _link_above(workspace, name, links) else _digest_or_missing(workspace, name)
    return found


def digest_of(files: Dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8", "surrogateescape") + b"\0" + files[name].encode("ascii") + b"\n")
    return digest.hexdigest()


def fingerprint(workspace: Path) -> str:
    """One digest for the whole workspace: 'what was checked' against 'what is there now'."""
    return digest_of(manifest(workspace))


def changed_files(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))


def closed_folders(workspace: Path, names: List[str]) -> List[str]:
    """The listed folders (names ending in a slash) that exist but cannot be read and searched now."""
    closed: List[str] = []
    for name in sorted(names):
        path = os.path.join(str(workspace), name.rstrip("/"))
        if name.endswith(DIR_SUFFIX) and os.path.isdir(path) and not os.path.islink(path) \
                and not os.access(path, os.R_OK | os.X_OK):
            closed.append(name)
    return closed


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
        finally:
            # Even a successful shell may have left writers in the background. Nothing it
            # started may outlive the acceptance snapshot or an interrupted runner.
            stop_group(child)
        sink.seek(0)
        output = sink.read().decode("utf-8", "replace")
    if code is None:
        output += f"\ncommand timed out after {timeout}s"
    return code, output


def stop_group(child: "subprocess.Popen[bytes]", grace: int = 5) -> None:
    """Ask a child's whole process group to stop, then always force it: a grandchild that ignores the
    polite signal must not outlive the turn even when the child itself has already gone."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            child.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass


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


def run_check(state: State, timeout: int = CHECK_TIMEOUT_SECONDS,
              before: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Run the acceptance command the two models agreed on, inside the workspace. `before` is the
    manifest the caller has just taken, when it has one: a large workspace is then not scanned twice."""
    workspace = Path(state["workspace"])
    before = manifest(workspace) if before is None else before
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
    if min(latest["seq"], latest["meta"].get("observed_seq", latest["seq"])) < after_seq:
        return latest, (f"{speaker} approved before the last acceptance run, a newer plan/criteria change, or a "
                        f"later message from the user; "
                        f"{speaker} must review again after receiving that evidence")
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


def review_boundary(state: State, messages: List[Message]) -> int:
    """Evidence and decisions the reviewers must have received before approving."""
    check = 0 if state.get("accept_waived") else (state.get("last_check") or {}).get("seq", 0)
    moved = [m["seq"] for m in messages if m["kind"] in ("accept", "replan", "command_error", "justify")]
    # Anything the user says may change what is wanted, so reviews given before it are stale.
    said = [m["seq"] for m in messages if m["speaker"] == "user" and m["kind"] == "message"]
    return max([check] + moved + said)


def finish_report(state: State, messages: List[Message], current: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Return (ready, reasons it is not ready). `current` is the workspace fingerprint when the caller
    has already taken it in this command."""
    current = fingerprint(Path(state["workspace"])) if current is None else current
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
    after_seq = review_boundary(state, messages)
    for speaker in reviewers:
        verdict, problem = review_standing(messages, speaker, after_seq)
        if problem:
            blocking.append(problem)
        elif verdict is not None and verdict["meta"].get("fingerprint") != current:
            blocking.append(f"files changed after {speaker} approved; {speaker} must review again")
    return (not blocking, blocking)
