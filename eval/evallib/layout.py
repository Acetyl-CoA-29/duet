"""Where a run's files live. Workspaces are kept apart from everything an arm must not read, so
that walking up from a workspace reaches no other arm's work, no results and no oracle.

    <work root>/<opaque id>/ws        one arm's throwaway workspace (default: under the temp folder)
    <work root>/../skill-<session>    the copy of the duet skill that hosts are pointed at
    <results>/<session>/...           records, logs, diffs, hidden output, the staged tasks
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .tasks import IGNORED_NAMES, Task, TaskError, copy_tree, load_task

# A workspace is <work root>/<id>/ws. Three `..` steps reach the work root's parent; nothing an
# arm must not read may sit inside that.
MIN_STEPS_APART = 3
SKILL_LEFT_OUT = ("eval", "tests", ".git", ".gitignore", ".coverage") + IGNORED_NAMES
SKILL_FILE = "SKILL.md"
WORKSPACE_NAME = "ws"


def stage_root() -> Path:
    """This user's own folder under the system temp folder."""
    return Path(tempfile.gettempdir()) / f"duet-eval-{os.getuid()}"


def default_work_root() -> Path:
    return stage_root() / "work"


def in_git_repository(path: Path) -> Optional[Path]:
    """The enclosing repository's root, if `path` (existing or not) would sit inside one."""
    current = path.resolve()
    for folder in (current, *current.parents):
        if (folder / ".git").exists():
            return folder
    return None


def steps_to_reach(start: Path, other: Path) -> int:
    """How many `..` steps from `start` lead to a folder that contains `other`."""
    start, other = start.resolve(), other.resolve()
    chain = (start, *start.parents)
    return next(steps for steps, folder in enumerate(chain) if folder == other or folder in other.parents)


def too_close(work_root: Path, other: Path) -> bool:
    """True when a workspace under `work_root` could reach `other` within MIN_STEPS_APART steps up."""
    return steps_to_reach(work_root / "run" / WORKSPACE_NAME, other) <= MIN_STEPS_APART


def check_apart(work_root: Path, kept_apart: Sequence[Path]) -> None:
    """Refuse a layout in which a workspace sits near the results, the logs or the tasks."""
    for other in kept_apart:
        if too_close(work_root, other):
            raise TaskError(f"the work folder {work_root} is too close to {other}: an arm could reach it by "
                            f"walking up from its workspace. Pass --work-dir somewhere else "
                            f"(the default is {default_work_root()})")


def new_workspace(work_root: Path) -> Tuple[Path, Path]:
    """A fresh (run folder, workspace path) pair with an opaque name; the workspace is not created yet."""
    run_dir = work_root / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True)
    return run_dir, run_dir / WORKSPACE_NAME


def stage_tasks(tasks: Sequence[Task], target: Path) -> List[Task]:
    """Copy the chosen tasks next to the results and load them from there, so nothing a run reads at
    run time sits in the skill checkout."""
    staged: List[Task] = []
    for task in tasks:
        copy = target / task.name
        if copy.exists():
            shutil.rmtree(str(copy))
        copy_tree(task.root, copy)
        staged.append(load_task(copy))
    return staged


def check_skill(skill_root: Path) -> None:
    if not (skill_root / SKILL_FILE).is_file():
        raise TaskError(f"{skill_root}: no {SKILL_FILE} there, so a duet arm has no skill to follow "
                        "(pass --skill-root)")


def stage_skill(skill_root: Path, target: Path) -> Path:
    """Copy the duet skill without its eval folder, tests and repository, and return the copy. Duet
    hosts are pointed at this copy, so no prompt names the checkout that holds the oracle. Any
    earlier copy is replaced, so nothing a previous arm wrote there survives."""
    check_skill(skill_root)
    if target.exists():
        shutil.rmtree(str(target))
    shutil.copytree(str(skill_root), str(target), ignore=shutil.ignore_patterns(*SKILL_LEFT_OUT))
    return target
