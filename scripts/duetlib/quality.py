"""Commands and rules that make the quality bar checkable: flags must be answered on the record,
and the acceptance command must have been seen to fail before a pass from it means anything."""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from . import facts, gates, phases
from . import state as st
from .base import EXIT_CHECK_FAILED, EXIT_OK, FINISHED_TEXT, emit, warn

PROOF_KEY = "accept_proof"
UNPROVEN = ("the acceptance command has never been seen to fail, so a pass from it proves nothing yet. "
            "Run `check` while the goal is still unmet (a failing run is the proof), or show it on a "
            "scratch copy with `probe --break \"<command that breaks the behaviour>\"`, or record the "
            "user's decision to go without with `probe --by-user \"<their words>\"`")
PROBE_TIMEOUT_SECONDS = 600
OUTPUT_TAIL_CHARS = 1500


def tracked(state: Dict[str, Any]) -> bool:
    """Runs started before this rule existed carry no proof field and are not held to it."""
    return PROOF_KEY in state


def proven(state: Dict[str, Any]) -> bool:
    """True when no proof is owed, or the current command is the one that was seen to fail."""
    command = state.get("accept_cmd")
    if not command or not tracked(state):
        return True
    return (state.get(PROOF_KEY) or {}).get("cmd") == command


def proof_change(state: Dict[str, Any], how: str) -> Dict[str, Any]:
    """State fields recording that the current acceptance command can fail, unless already recorded."""
    if not tracked(state) or proven(state):
        return {}
    return {PROOF_KEY: {"cmd": state["accept_cmd"], "how": how}}


def blocker(state: Dict[str, Any]) -> Optional[str]:
    return None if proven(state) else UNPROVEN


def _phase(run: Path, state: Dict[str, Any]) -> str:
    return phases.current(state, st.read_messages(run))


def _active(run: Path) -> Dict[str, Any]:
    state = st.load_state(run)
    if state["status"] != "active":
        raise st.DuetError(FINISHED_TEXT)
    return state


def cmd_justify(args: argparse.Namespace) -> int:
    """Put on the record why a runner flag is right as it stands. Both models then review again."""
    run = Path(args.run)
    state = _active(run)
    found = facts.report(run, state, Path(state["workspace"]))
    match = next((f for f in found["findings"] if f["id"] == args.flag), None)
    if match is None:
        known = ", ".join(f["id"] for f in found["findings"]) or "none"
        raise st.DuetError(f"no flag named {args.flag!r} is raised on the current files (raised now: {known})")
    text = (f"Flag justified by {state['host']}: {match['id']} ({match['detail']}). Reason: {args.reason.strip()}\n"
            "Reviews given before this do not count: the reviewer must agree the flagged code is right.")
    entry = {"detail": match["detail"], "reason": args.reason.strip()}
    st.append_message(run, "system", _phase(run, state), text, kind="justify", change=lambda fresh, seq: {
        "justified": {**(fresh.get("justified") or {}), match["id"]: {**entry, "seq": seq}}})
    emit(f"justification recorded for {match['id']}; it needs fresh reviews")
    return EXIT_OK


def _copy_of(workspace: Path, parent: Path) -> Path:
    target = parent / "workspace"
    shutil.copytree(str(workspace), str(target), symlinks=True,
                    ignore=shutil.ignore_patterns(st.RUN_ROOT))
    return target


def _probe(state: Dict[str, Any], break_cmd: str, timeout: int) -> Dict[str, Any]:
    """Break a scratch copy of the workspace, then run the acceptance command there."""
    with tempfile.TemporaryDirectory(prefix="duet-probe-") as scratch:
        copy = _copy_of(Path(state["workspace"]), Path(scratch))
        broke, broke_out = gates.run_command(break_cmd, copy, timeout)
        if broke != 0:
            return {"ran": False, "exit": broke, "output": broke_out}
        code, output = gates.run_command(state["accept_cmd"], copy, timeout)
        return {"ran": True, "exit": code, "output": output}


def cmd_probe(args: argparse.Namespace) -> int:
    """Show that the acceptance command fails when the behaviour is broken, without touching the workspace."""
    run = Path(args.run)
    state = _active(run)
    if not state.get("accept_cmd"):
        raise st.DuetError("no acceptance command is set; there is nothing to prove")
    if bool(args.break_cmd) == bool(args.by_user):
        raise st.DuetError("give exactly one of --break \"<command>\" or --by-user \"<their words>\"")
    if args.by_user:
        text = (f"The user accepts `{state['accept_cmd']}` without proof that it can fail: {args.by_user.strip()}")
        st.append_message(run, "system", _phase(run, state), text, kind="probe",
                          change=lambda fresh, seq: proof_change(fresh, "accepted unproven by the user"))
        emit("recorded: the acceptance command is accepted without proof, by the user's decision")
        return EXIT_OK
    try:
        result = _probe(state, args.break_cmd, args.timeout)
    except (OSError, shutil.Error) as exc:
        raise st.DuetError(f"could not make a scratch copy of the workspace for the probe: {exc}") from exc
    tail = result["output"][-OUTPUT_TAIL_CHARS:]
    if not result["ran"]:
        warn(f"the break command itself failed (exit {result['exit']}), so nothing was proven:\n{tail}")
        return EXIT_CHECK_FAILED
    caught = result["exit"] not in (0, None)
    outcome = (f"FAILED as it should (exit {result['exit']})" if caught else
               "timed out, which proves nothing" if result["exit"] is None else
               "STILL PASSED: it does not detect this breakage and must be strengthened")
    text = (f"Probe on a scratch copy: after `{args.break_cmd}`, acceptance `{state['accept_cmd']}` {outcome}."
            f"\n\n```\n{tail}\n```")
    st.append_message(run, "system", _phase(run, state), text, kind="probe", meta={"caught": caught},
                      change=lambda fresh, seq: proof_change(fresh, f"probe at message {seq}") if caught else {})
    emit(text)
    return EXIT_OK if caught else EXIT_CHECK_FAILED
