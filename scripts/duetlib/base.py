"""What every command shares: exit codes, output, atomic state updates and the one-turn-at-a-time lock."""
from __future__ import annotations

import fcntl
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator

from . import state as st
from . import store

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_USAGE = 2
EXIT_CAP = 3
EXIT_STALLED = 4
EXIT_PEER_FAILED = 5
EXIT_ORDER = 6  # a phase asked for out of order, the blind round included
EXIT_BLIND = EXIT_ORDER
EXIT_NOT_DONE = 7
EXIT_READONLY_BROKEN = 8
EXIT_TAMPERED = 9
EXIT_INTERNAL = 70

FINISHED_TEXT = "this run is finished; start a new one"
PEER_LOCK_FILE = ".peer.lock"
BUSY_TEXT = ("a peer turn is already running for this run. Wait for it to finish, or stop that `peer` "
             "command, before starting another: two turns cannot share one session")

Change = Callable[[st.State], Dict[str, Any]]


def emit(text: str) -> None:
    sys.stdout.write(text.rstrip("\n") + "\n")


def warn(text: str) -> None:
    sys.stderr.write(f"duet: {text}\n")


@contextmanager
def transaction(run: Path, active: bool = True) -> Iterator[st.State]:
    """Validate and commit under one lock; long commands use this for admission and receipt."""
    with st.locked(run):
        st.verify_integrity(run)
        fresh = st.reconcile(run)
        if active and fresh["status"] != "active":
            raise st.DuetError(FINISHED_TEXT)
        yield fresh


def update(run: Path, change: Change) -> st.State:
    """Reload the state under the run's lock and replace only the fields `change` returns.

    A command that ran for minutes must not write back the state it loaded when it started:
    another command may have saved a peer turn, a message or a check in the meantime.
    """
    shared = getattr(st, "update_state", None)
    if shared is not None:
        return shared(run, change)
    with st.locked(run):
        fresh = st.load_state(run)
        new = st.updated(fresh, **change(fresh))
        st._write_atomic(run / st.STATE_FILE, json.dumps(new, indent=2, sort_keys=True) + "\n")
        store.reseal(run)
    return new


@contextmanager
def peer_lock(run: Path) -> Iterator[bool]:
    """Try to take the run's peer-turn lock without waiting. Yields False when a turn already holds it."""
    with (run / PEER_LOCK_FILE).open("a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
