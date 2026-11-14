"""After-the-fact checks on what an arm printed and wrote: did it name the oracle or reach outside
its workspace, and did the run fail for a reason that says nothing about the model.

These are audits, not prevention. A client whose sandbox allows reading the whole disk can still
read a file by its absolute path; what it printed while doing so is what gets flagged here."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .tasks import Task

MAX_SCAN_BYTES = 5_000_000
MAX_HITS = 20
MIN_FILE_NAME_CHARS = 8
SOLUTION_FRAGMENTS = ("solutions/reference", "solutions/naive")
# A path that climbs out of the folder it is used in: `../x`, `../../logs/y`.
RELATIVE_ESCAPE = re.compile(r"(?:^|[\s\"'`=(:\[])((?:\.\./)+[\w.\-/]*)")
# Lower-case text that marks a failure of the tooling rather than of the model's work.
INFRA_PATTERNS = ("operation not permitted", "permission denied", "usage limit", "rate limit", "rate_limit",
                  "quota", "not logged in", "please run /login", "unauthorized", "invalid api key",
                  "authentication", "overloaded", "command not found")
NOT_STARTED = "the client could not be started"
NO_WORK = "the client failed before doing any work"
NO_DUET = "no duet took place"


def _texts(sources: Sequence[Path]) -> Iterator[Tuple[str, str]]:
    """(file name, text) for every readable file in the sources that is small enough to scan."""
    for source in sources:
        files = [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.is_file()) if source.is_dir() else []
        for path in files:
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    continue
                yield path.name, path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue


def oracle_needles(task: Task, watch: Sequence[Path] = ()) -> List[str]:
    """Strings that have no business in an arm's output: the task's own folder and the watched
    folders by absolute path, and the oracle by the relative names it could be reached under."""
    absolute = [str(task.root)] + [str(path) for path in watch]
    starter_names = {p.name for p in task.starter.rglob("*") if p.is_file()}
    hidden_names = sorted({p.name for p in task.hidden.rglob("*") if p.is_file()
                           and len(p.name) >= MIN_FILE_NAME_CHARS and p.name not in starter_names})
    relative = [f"{task.name}/hidden", f"{task.name}/solutions", *SOLUTION_FRAGMENTS]
    return absolute + relative + hidden_names


def oracle_paths_seen(task: Task, sources: Sequence[Path], watch: Sequence[Path] = ()) -> List[str]:
    """Which oracle names appear in what the arm printed or wrote, as "<file>: <name>"."""
    needles = oracle_needles(task, watch)
    seen = {f"{name}: {needle}" for name, text in _texts(sources) for needle in needles if needle in text}
    return sorted(seen)


def outside_paths_seen(sources: Sequence[Path]) -> List[str]:
    """Relative paths that climb out of the workspace (`../...`), as "<file>: <path>". Weaker
    evidence than an oracle name: it shows the arm looked outside, not what it found."""
    seen = {f"{name}: {match}" for name, text in _texts(sources) for match in RELATIVE_ESCAPE.findall(text)}
    return sorted(seen)[:MAX_HITS]


def _matched(evidence: str) -> Optional[str]:
    lowered = evidence.lower()
    return next((pattern for pattern in INFRA_PATTERNS if pattern in lowered), None)


def _no_duet_reason(duet: Dict[str, Any], evidence: str) -> str:
    if duet.get("found") is not None:
        return f"{NO_DUET}: the peer never took a turn"
    pattern = _matched(evidence)
    return f"{NO_DUET}: {pattern}" if pattern else f"{NO_DUET}: no run was recorded for this workspace"


def infra_failure(status: str, changed: bool, usage_parsed: bool, evidence: str,
                  duet: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why this run says nothing about the model, or None when it does.

    `evidence` is the client's error output and final message. A run that hit its time limit is
    not an infrastructure failure: it is counted on its own."""
    if status == "not_started":
        return NOT_STARTED
    if duet is not None and duet.get("ran") is False:
        return _no_duet_reason(duet, evidence)
    if status != "error":
        return None
    pattern = _matched(evidence)
    if pattern:
        return f"the client failed: {pattern}"
    return NO_WORK if not changed and not usage_parsed else None
