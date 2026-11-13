"""Run one child process with a wall-clock limit, stopping its whole process group on timeout."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

KILL_GRACE_SECONDS = 5
EXIT_NOT_STARTED = 127


@dataclass(frozen=True)
class ProcResult:
    exit_code: Optional[int]  # None when the process was stopped for running over time
    timed_out: bool
    seconds: float
    error: str = ""


def stop_group(child: "subprocess.Popen[bytes]", grace: float = KILL_GRACE_SECONDS) -> None:
    """Ask the child's whole process group to stop, then force it. The group is signalled even
    after the leader has gone, so anything it started does not outlive the run."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            child.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        if not _group_alive(child.pid):
            break
        time.sleep(min(grace, 0.2))
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def run(argv: List[str], cwd: Path, timeout: float, stdout: Path, stderr: Path,
        stdin_text: Optional[str] = None, env: Optional[Dict[str, str]] = None,
        grace: float = KILL_GRACE_SECONDS) -> ProcResult:
    """Run `argv` (never through a shell) in its own process group. Any input goes in on stdin."""
    started = time.monotonic()
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            child = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=out, stderr=err,
                                     stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                     start_new_session=True)
            try:
                child.communicate(stdin_text.encode("utf-8") if stdin_text is not None else None,
                                  timeout=timeout)
            except subprocess.TimeoutExpired:
                stop_group(child, grace)
                return ProcResult(None, True, time.monotonic() - started,
                                  f"stopped after the {timeout:g}s limit")
            except BaseException:
                stop_group(child, grace)
                raise
            return ProcResult(child.returncode, False, time.monotonic() - started)
    except OSError as exc:
        return ProcResult(EXIT_NOT_STARTED, False, time.monotonic() - started,
                          f"could not start {argv[0]}: {exc}")
