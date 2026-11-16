"""Admission and crash recovery for acceptance checks and agreed experiments."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import authors, base, gates, phases, state as st, turn


class CommandInterrupted(st.DuetError):
    """A stopped command is a failed check/experiment, not a completed receipt."""


def _abandon(run: Path, reason: str) -> None:
    with base.transaction(run) as state:
        pending = state.get("pending_command")
        if not pending:
            return
        messages = st.read_messages(run)
        current = gates.fingerprint(Path(state["workspace"]))
        text = f"{pending['kind'].capitalize()} interrupted: {reason}. Run it again before relying on its result."
        st.append_message(run, "system", phases.current(state, messages), text, kind="command_error",
                          change=lambda fresh, seq: {
                              "pending_command": None,
                              **({"last_check": None} if pending["kind"] == "check" else {}),
                              **authors.observed(fresh, messages, current, fresh["host"]),
                          })
        base.warn(text)


@contextmanager
def running(run: Path, kind: str) -> Iterator[st.State]:
    """Keep finish blocked from admission through the caller's committed output receipt."""
    with base.command_lock(run) as mine:
        if not mine:
            raise st.DuetError("a check or experiment is already running; wait for its receipt")
        with base.transaction(run) as state:
            state = base.update(run, lambda fresh: {
                "pending_command": {"kind": kind, "started": st.now_iso()},
            })
        try:
            with turn._stoppable():
                yield state
        except BaseException as exc:
            with turn._unstoppable():
                _abandon(run, f"{type(exc).__name__}: {exc}")
            if isinstance(exc, turn.Stopped):
                raise CommandInterrupted(str(exc)) from exc
            raise


def settle_if_abandoned(run: Path) -> None:
    if not st.load_state(run).get("pending_command"):
        return
    with base.command_lock(run) as mine:
        if mine:
            _abandon(run, "the runner stopped before recording output; stop any surviving command process")
