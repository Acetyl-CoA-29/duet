"""Task format: load a task directory and lay out a fresh workspace from its starter tree.

A task directory holds:
  task.md      the goal handed to the model
  task.json    optional: {"accept": "<visible command>", "hidden_cmd": "...", "protected": [...]}
  accept.sh    alternative to "accept" in task.json; copied into the workspace and run with sh
  starter/     copied into the workspace
  hidden/      oracle tests; never copied into the workspace, only into a scoring copy afterwards
  solutions/   optional reference/ and naive/ overlays used by `verify` (never copied into a run)
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HIDDEN_DIRNAME = "hidden_tests"
DEFAULT_HIDDEN_CMD = f"python3 -m pytest -q -p no:cacheprovider {HIDDEN_DIRNAME}"
ACCEPT_SCRIPT = "accept.sh"
IGNORED_NAMES = ("__pycache__", ".pytest_cache", ".DS_Store")
GIT_IDENTITY = ["-c", "user.name=duet-eval", "-c", "user.email=duet-eval@example.invalid",
                "-c", "commit.gpgsign=false"]
GIT_TIMEOUT_SECONDS = 60


class TaskError(Exception):
    """A task directory is missing something or is malformed."""


@dataclass(frozen=True)
class Task:
    name: str
    root: Path
    goal: str
    accept: str
    hidden_cmd: str
    protected: Tuple[str, ...]

    @property
    def starter(self) -> Path:
        return self.root / "starter"

    @property
    def hidden(self) -> Path:
        return self.root / "hidden"

    def solution(self, kind: str) -> Path:
        return self.root / "solutions" / kind


def _read_config(root: Path) -> Dict[str, Any]:
    path = root / "task.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TaskError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise TaskError(f"{path}: expected a JSON object")
    return data


def load_task(root: Path) -> Task:
    root = root.resolve()
    for needed in ("task.md", "starter", "hidden"):
        if not (root / needed).exists():
            raise TaskError(f"{root}: missing {needed}")
    config = _read_config(root)
    has_script = (root / ACCEPT_SCRIPT).is_file()
    accept = config.get("accept") or (f"sh {ACCEPT_SCRIPT}" if has_script else "")
    if not isinstance(accept, str) or not accept.strip():
        raise TaskError(f"{root}: give an \"accept\" command in task.json or an {ACCEPT_SCRIPT}")
    goal = (root / "task.md").read_text(encoding="utf-8")
    if not goal.strip():
        raise TaskError(f"{root}: task.md is empty")
    protected = tuple(str(p) for p in config.get("protected", []))
    if has_script and ACCEPT_SCRIPT not in protected:
        protected = protected + (ACCEPT_SCRIPT,)
    return Task(root.name, root, goal, accept, config.get("hidden_cmd") or DEFAULT_HIDDEN_CMD, protected)


def discover(tasks_dir: Path, names: Optional[List[str]] = None) -> List[Task]:
    """Tasks under a directory, in name order; `names` selects and orders a subset."""
    if not tasks_dir.is_dir():
        raise TaskError(f"{tasks_dir}: no such tasks directory")
    found = {p.name: p for p in sorted(tasks_dir.iterdir()) if (p / "task.md").is_file()}
    if names is None:
        return [load_task(path) for path in found.values()]
    missing = [name for name in names if name not in found]
    if missing:
        raise TaskError(f"unknown task(s): {', '.join(missing)}; available: {', '.join(found) or 'none'}")
    return [load_task(found[name]) for name in names]


def copy_tree(source: Path, target: Path) -> None:
    """Copy a tree over `target`, replacing files that exist and skipping caches."""
    shutil.copytree(str(source), str(target), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*IGNORED_NAMES))


def prepare_workspace(task: Task, workspace: Path) -> None:
    """Fresh workspace: the starter tree, the visible acceptance script if any, and nothing else."""
    if workspace.exists():
        raise TaskError(f"{workspace}: workspace already exists; each run needs a fresh one")
    workspace.mkdir(parents=True)
    copy_tree(task.starter, workspace)
    script = task.root / ACCEPT_SCRIPT
    if script.is_file():
        shutil.copy2(str(script), str(workspace / ACCEPT_SCRIPT))
    _git_baseline(workspace)


def _git(workspace: Path, *args: str) -> "subprocess.CompletedProcess[bytes]":
    return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=str(workspace), capture_output=True,
                          timeout=GIT_TIMEOUT_SECONDS, check=False)


def _git_baseline(workspace: Path) -> bool:
    """Make the workspace its own repository so no client treats a parent folder as the project,
    and so the finished work can be saved as a diff. Skipped quietly when git is unavailable."""
    try:
        if _git(workspace, "init", "-q").returncode != 0:
            return False
        (workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.duet/\n", encoding="utf-8")
        # A Codex host's sandbox keeps .git read-only, and the duet runner stops if it cannot add
        # its folder to the local exclude list; listing it up front means it has nothing to write.
        exclude = workspace / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write("\n.duet/\n")
        _git(workspace, "add", "-A")
        return _git(workspace, "commit", "-q", "-m", "starter").returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def workspace_diff(workspace: Path) -> str:
    """Everything the arm changed relative to the starter, as a patch; empty when git is unavailable."""
    try:
        _git(workspace, "add", "-A", "-N")
        done = _git(workspace, "diff", "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.decode("utf-8", errors="replace") if done.returncode == 0 else ""
