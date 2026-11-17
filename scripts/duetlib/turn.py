"""One peer turn: what is recorded before it starts, how it ends, and how a turn that never ended is settled."""
from __future__ import annotations

import argparse
import json
import os
import signal
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import auth, authors, base, facts, gates, models, peers, phases, prompts, quality, snapshot, store
from . import state as st
from .base import (EXIT_BLIND, EXIT_CAP, EXIT_OK, EXIT_ORDER, EXIT_PEER_FAILED, EXIT_READONLY_BROKEN, EXIT_STALLED,
                   EXIT_USAGE, FINISHED_TEXT, emit, warn)

READONLY_TEXT = ("Files in the workspace changed during {peer}'s read-only turn: {names}. The peer is the likely "
                 "author, but an edit by the user or another process during the turn looks the same. ")
READONLY_RESTORED = ("They were put back exactly as they were; the changed versions are saved in {evidence}. "
                     "Any approval in that turn is not counted.")
READONLY_LEFT = ("{why}, so the change is still in place; a listing is saved in {evidence}. "
                 "Inspect it before continuing; any approval in that turn is not counted.")
RECOVER_ACCESS = " To recover by hand, give yourself access to these folders again: chmod u+rwx {paths}."
FOLDER_KEPT = " (a folder that replaced a file could not be saved whole first, so it was not removed)"
NO_SNAPSHOT = {
    "ceiling": "No snapshot was taken (workspace over the size ceiling)",
    "sandbox": ("No snapshot was taken (the peer's own sandbox refuses writes, so this change most likely came "
                "from the user or another process)"),
}
VIOLATION_DIR = "readonly-violation"
REOPEN_ROUNDS = 64
REPLACED_DIR = "folders"  # inside the evidence folder: whole folders that stood where a file was
SNAPSHOT_DIR = "snapshot"
BEFORE_FILE = "before.json"
NAMES_SHOWN = 10
BLIND_PHASES = ("frame", "propose")  # the host's sealed proposal is not released in these
TURN_CHANGED = (" Workspace files changed during the turn ({names}), so its work may be complete on disk: "
                "run `check` before deciding what to do next.")
TURN_UNCHANGED = " No workspace file changed during the turn."
STOPPED_BY_SIGNAL = "the runner was stopped (signal {signum}) while the peer was working, and the peer was stopped with it"
STOPPED_UNSEEN = ("the runner was stopped before the turn could be recorded; a peer process from that turn may "
                  "still be running and should be stopped")
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class Stopped(BaseException):
    """The runner was told to stop while a peer turn was in flight."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"signal {signum}")
        self.signum = signum


def _stop(signum: int, _frame: Any) -> None:
    raise Stopped(signum)


@contextmanager
def _stoppable() -> Iterator[None]:
    """Turn a stop signal into an exception, so the peer is stopped and the turn settled before exit."""
    previous: Dict[int, Any] = {}
    try:
        for sig in STOP_SIGNALS:
            previous[sig] = signal.signal(sig, _stop)
    except ValueError:  # not the main thread: signals cannot be handled here, the turn runs unguarded
        pass
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@contextmanager
def _unstoppable() -> Iterator[None]:
    """Ignore stop signals while a turn is being settled, so the clean-up is not itself cut short."""
    previous: Dict[int, Any] = {}
    try:
        for sig in STOP_SIGNALS:
            previous[sig] = signal.signal(sig, signal.SIG_IGN)
    except ValueError:
        pass
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _names(changed: List[str]) -> str:
    extra = len(changed) - NAMES_SHOWN
    return ", ".join(changed[:NAMES_SHOWN]) + (f" and {extra} more" if extra > 0 else "")


def order_problem(
    state: st.State, messages: List[st.Message], phase: Optional[str], replan: Optional[str], write: bool = False,
) -> Optional[Tuple[int, str]]:
    """Why a host message or a peer turn may not act in this phase now: (exit code, what to say)."""
    if state["status"] != "active":
        return EXIT_USAGE, FINISHED_TEXT
    if phase is None:
        return EXIT_USAGE, f"--phase is required for {state['host']}'s own message"
    here = phases.current(state, messages)
    if replan is not None and not (replan.strip() and phase == phases.PLAN and here in phases.REPLAN_FROM):
        return EXIT_USAGE, '--replan "<reason>" is only for going back to plan from build or review'
    if write and phase != phases.BUILD:
        return EXIT_ORDER, "--write is only for build turns: the peer edits files in `--phase build` alone"
    if here == phases.FRAME and phase != phases.FRAME and not quality.proven(state):
        return EXIT_ORDER, "the run cannot leave frame yet: " + quality.UNPROVEN
    problem = phases.refusal(state, messages, phase, replan)
    if problem:
        allowed = ", ".join(phases.allowed_next(state, messages))
        return EXIT_ORDER, f"{problem}. Allowed now: {allowed}"
    return None


def record_replan(run: Path, here: str, reason: str) -> None:
    st.append_message(run, "system", phases.PLAN, f"Back to plan from {here}: {reason.strip()}",
                      kind=phases.REPLAN_KIND, change=lambda fresh, seq: {"phase": phases.PLAN, "replan_seq": seq})


def _delta(state: st.State, messages: List[st.Message], phase: str) -> Tuple[List[st.Message], List[int]]:
    """Messages the peer has not seen. In the blind phase the host's proposal is held back."""
    pending = [
        m for m in messages
        if m["speaker"] != state["peer"] and (m["seq"] > state["peer_cursor"] or m["seq"] in state["withheld"])
    ]
    if phase not in BLIND_PHASES:
        return pending, []
    held = [m["seq"] for m in pending if m["speaker"] == state["host"] and m["phase"] == "propose"]
    return [m for m in pending if m["seq"] not in held], held


def _peer_blocker(
    state: st.State, messages: List[st.Message], phase: str, write: bool = False, replan: Optional[str] = None,
) -> Optional[Tuple[int, str]]:
    if state["status"] != "active":
        return EXIT_USAGE, FINISHED_TEXT
    if state["peer_turns"] >= state["max_turns"]:
        return EXIT_CAP, (f"turn cap reached ({state['max_turns']}). Stop and report to the user; "
                          "continue only if they say so, with `extend --turns`")
    if gates.is_stalled(state, messages):
        return EXIT_STALLED, (f"the acceptance command failed {gates.STALL_AFTER} times the same way. "
                              "Stop and ask the user; record their answer with `say --speaker user`")
    cap, spent = state.get("max_cost_usd"), state.get("peer_usage", {}).get("cost_usd", 0.0)
    if cap is not None and spent >= cap:
        return EXIT_CAP, (f"cost cap reached (${spent:.2f} of ${cap:.2f} reported by the peer client). "
                          "Stop and report to the user; continue only if they say so, with `extend --cost`")
    out_of_order = order_problem(state, messages, phase, replan, write)
    if out_of_order:
        return out_of_order
    host_proposed = any(m["speaker"] == state["host"] and m["phase"] == "propose" for m in messages)
    if phase == "propose" and not host_proposed:
        return EXIT_BLIND, (f"blind round: record {state['host']}'s own proposal first "
                            "(`say --phase propose`), then ask the peer")
    if write and gates.proposals_missing(state, messages):
        return EXIT_BLIND, ("no build turn before the blind proposal round: both models must record a "
                            "proposal first (or the run must have been started with --skip-proposals)")
    return None


TOKEN_KEYS = ("input_tokens", "output_tokens")


def _add_usage(total: Optional[Dict[str, Any]], turn: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    keys = TOKEN_KEYS + ("cost_usd",)
    return {key: (total or {}).get(key, 0) + (turn or {}).get(key, 0) for key in keys}


def _usage_change(fresh: st.State, session: Optional[str], usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """State fields that add one turn's reported usage to the run's total.

    The Claude client reports each invocation's own tokens and cost, which are simply added. The Codex
    client reports the running total of the whole thread, so only the increase since the last report from
    the same session is added; another session (a first turn, or --new-session) starts from zero.
    """
    total = fresh.get("peer_usage")
    reported = {key: (usage or {}).get(key, 0) for key in TOKEN_KEYS}
    if fresh["peer"] != "codex":
        return {"peer_usage": _add_usage(total, usage)}
    if not any(reported.values()):
        return {"peer_usage": _add_usage(total, None)}  # nothing reported: the baseline stands
    base = fresh.get("peer_usage_base") or {}
    same = session is not None and base.get("session") == session
    added = {}
    for key in TOKEN_KEYS:
        prior = base.get(key, 0) if same else 0
        added[key] = reported[key] - prior if reported[key] >= prior else reported[key]
    return {"peer_usage": _add_usage(total, added), "peer_usage_base": {"session": session, **reported}}


def _snapshot(peer: str, workspace: Path, files: Dict[str, str], turn_dir: Path) -> str:
    """Copy the workspace's source files before a read-only turn. Returns 'taken', or why there is none:
    'sandbox' when the peer's own sandbox already refuses writes, 'ceiling' when it is too large to copy."""
    if peers.enforces_read_only(peer):
        return "sandbox"
    if snapshot.take(workspace, files, turn_dir / SNAPSHOT_DIR):
        return "taken"
    warn(f"the workspace is over the snapshot ceiling ({snapshot.max_bytes() // (1024 * 1024)} MB, "
         f"{snapshot.MAX_ENV}) or could not be copied: a change in this read-only turn will be reported "
         "but not undone")
    return "ceiling"


def _keep_unsaved(workspace: Path, evidence: Path, changed: List[str]) -> List[str]:
    """Make sure no changed version is lost: one that could not be copied aside is moved aside instead.

    Returns the names that could be neither copied nor moved; those must not be overwritten.
    """
    stuck: List[str] = []
    for name in changed:
        current, kept = workspace / name, evidence / "files" / name
        if snapshot._link_above(workspace, current):
            continue  # a tracked descendant of a replaced directory may now name an external file
        is_file = os.path.lexists(current) and (current.is_symlink() or not current.is_dir())
        if not is_file or os.path.lexists(kept):
            continue
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            os.replace(current, kept)
        except OSError:
            stuck.append(name)
    return stuck


def _undo_read_only_change(
    state: st.State, workspace: Path, turn_dir: Path, snap: Optional[Path],
    before_files: Optional[Dict[str, str]], after_files: Dict[str, str], no_snapshot: str = "ceiling",
) -> Tuple[str, str]:
    """Save what changed, put the files back, and return (what to record, fingerprint now)."""
    evidence = turn_dir / VIOLATION_DIR
    if before_files is None:
        head = READONLY_TEXT.format(peer=state["peer"], names="(the list from before the turn was lost)")
        why = "The record of the files before the turn could not be read"
        return head + READONLY_LEFT.format(why=why, evidence=evidence), gates.digest_of(after_files)
    if snap is not None:
        # A folder whose permissions were taken away hides what is inside it: open it first, look again.
        closed_view: Dict[str, str] = {}
        for _ in range(REOPEN_ROUNDS):  # each round can uncover closed folders one level further down
            reopened = snapshot.reopen(workspace, snap, before_files, after_files)
            if not reopened:
                break
            for name in reopened:
                closed_view.setdefault(name, after_files.get(name, gates.MISSING))
            after_files = gates.manifest(workspace)
        after_files = {**after_files, **closed_view}
    changed = gates.changed_files(before_files, after_files)
    snapshot.save_evidence(workspace, snap, changed, before_files, after_files, evidence)
    head = READONLY_TEXT.format(peer=state["peer"], names=_names(changed))
    if snap is None:
        left = READONLY_LEFT.format(why=NO_SNAPSHOT.get(no_snapshot, NO_SNAPSHOT["ceiling"]), evidence=evidence)
        return head + left + _recover_note(workspace, before_files), gates.digest_of(after_files)
    unsaved = _keep_unsaved(workspace, evidence, changed)  # never overwrite a version that was not kept
    failed = unsaved + snapshot.restore(workspace, snap, [name for name in changed if name not in unsaved],
                                        before_files, aside=evidence / REPLACED_DIR)
    now = gates.fingerprint(workspace)
    if failed or now != gates.digest_of(before_files):
        why = "Restoring failed for " + (_names(failed) or "some files")
        if unsaved:
            why += " (a changed file could be neither copied nor moved aside first, so it was not overwritten)"
        if any(not name.endswith("/") and gates._real_dir(workspace / name) for name in failed):
            why += FOLDER_KEPT
        kept = turn_dir / snapshot.KEPT_NAME
        left = READONLY_LEFT.format(why=why, evidence=evidence)
        saved = f" The files as they were before the turn are kept in {kept}." if kept.is_dir() else ""
        return head + left + saved + _recover_note(workspace, before_files), now
    return head + READONLY_RESTORED.format(evidence=evidence), now


def _recover_note(workspace: Path, before_files: Dict[str, str]) -> str:
    """What to run by hand when folders are still closed to their owner after a turn."""
    closed = gates.closed_folders(workspace, list(before_files))
    if not closed:
        return ""
    return RECOVER_ACCESS.format(paths=" ".join(f"'{workspace / name.rstrip('/')}'" for name in closed[:NAMES_SHOWN]))


def _failed_turn_note(before: Optional[Dict[str, str]], after: Dict[str, str], violation: str) -> str:
    """What a failed peer turn left on disk; a read-only turn's change is reported by its own message."""
    if violation:
        return ""
    if before is None:
        return TURN_CHANGED.format(names="list unavailable")
    changed = gates.changed_files(before, after)
    return TURN_CHANGED.format(names=_names(changed)) if changed else TURN_UNCHANGED


def _load_before(turn_dir: Path) -> Optional[Dict[str, str]]:
    try:
        loaded = json.loads((turn_dir / BEFORE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _close_files(state: st.State, pending: Dict[str, Any], workspace: Path, turn_dir: Path,
                 before_files: Optional[Dict[str, str]]) -> Tuple[str, str, Dict[str, str]]:
    """Compare the workspace with how the turn found it, undo a read-only turn's change and drop the snapshot.

    Returns (violation text or '', fingerprint now, manifest right after the turn).
    """
    snap = turn_dir / SNAPSHOT_DIR
    after_files = gates.manifest(workspace)
    after = gates.digest_of(after_files)
    violation = ""
    if not pending["write"] and after != pending["before"]:
        violation, after = _undo_read_only_change(state, workspace, turn_dir, snap if snap.is_dir() else None,
                                                  before_files, after_files, pending.get("snapshot", "ceiling"))
    if snap.is_dir() and (before_files is None or after != pending["before"]):
        snapshot.keep(snap)
    else:
        snapshot.discard(snap)
    return violation, after, after_files


def _authorship(pending: Dict[str, Any], after: str, messages: List[st.Message], fresh: st.State) -> Dict[str, Any]:
    """A change that outlived the turn is the peer's, whoever else's edits were already on record."""
    if after == pending["before"]:
        return {}
    return authors.observed(fresh, messages, after, fresh["peer"], force=True)


def settle_pending(run: Path, why: str) -> Optional[str]:
    """Close a peer turn that started but was never recorded: count it, undo or attribute its changes.

    Returns the note that was put on the record, or None when no turn was pending.
    """
    with base.transaction(run, active=False) as state:
        return _settle_pending(run, why, state)


def _settle_pending(run: Path, why: str, state: st.State) -> Optional[str]:
    pending = state.get("pending_turn")
    if not pending:
        return None
    turn_dir = run / "turns" / pending["dir"]
    before_files = _load_before(turn_dir)
    violation, after, after_files = _close_files(state, pending, Path(state["workspace"]), turn_dir, before_files)
    unchanged = not violation and gates.digest_of(after_files) == pending["before"]
    note = TURN_UNCHANGED if unchanged else _failed_turn_note(before_files, after_files, violation)
    text = f"Peer turn {pending['attempt']} ({pending['phase']}) did not complete: {why}.{note}"
    messages = st.read_messages(run)
    saved = peers.saved_result(state["peer"], turn_dir)
    usage = saved.usage

    def change(fresh: st.State, _seq: int) -> Dict[str, Any]:
        primed = bool(fresh.get("peer_primed", fresh["peer_session"] is not None)) and not pending.get("replay")
        return {"pending_turn": None, "peer_turns": fresh["peer_turns"] + 1, "last_peer_failed": primed,
                "peer_session": fresh["peer_session"] if primed else None,
                "peer_replay": bool(pending.get("replay") or fresh.get("peer_replay")),
                **_usage_change(fresh, saved.session or (None if pending.get("replay") else fresh["peer_session"]),
                                usage),
                **_authorship(pending, after, messages, fresh)}

    st.append_message(run, "system", pending["phase"], text, kind="peer_error",
                      meta={"usage": usage or {}}, change=change)
    if violation:
        st.append_message(run, "system", pending["phase"], violation, kind="readonly_violation")
    return text + (" " + violation if violation else "")


def _prepare(run: Path, args: argparse.Namespace, state: st.State, messages: List[st.Message]) -> Dict[str, Any]:
    """Everything a turn needs before the peer starts, with the pending-turn record saved."""
    workspace = Path(state["workspace"])
    before_files = gates.manifest(workspace)
    before = gates.digest_of(before_files)
    replay = bool(args.new_session or state.get("peer_replay"))
    session = None if replay else state["peer_session"]
    if replay:
        # Rebuild the peer's memory from the record: everything said so far, its own words included.
        hidden = set(_delta(state, messages, args.phase)[1])
        delta, held = [m for m in messages if m["seq"] not in hidden and m["kind"] != "goal"], sorted(hidden)
    else:
        delta, held = _delta(state, messages, args.phase)
    changes = gates.change_summary(workspace) if args.phase == "review" else ""
    computed = facts.for_reviewer(state, facts.report(run, state, workspace)) if args.phase == "review" else ""
    plan_mode = state["peer"] == "claude" and peers.claude_uses_plan_mode(args.write)
    prompt = prompts.build_prompt(state, next((m["text"] for m in messages if m["kind"] == "goal"), ""),
                                  args.phase, delta, args.write, session is None,
                                  retry=bool(state.get("last_peer_failed")) and session is not None,
                                  changes=changes, plan_mode=plan_mode, facts=computed)
    attempt = len(list((run / "turns").iterdir())) + 1
    turn_dir = run / "turns" / f"{attempt:03d}"
    turn_dir.mkdir(parents=True, exist_ok=True)
    (turn_dir / BEFORE_FILE).write_text(json.dumps(before_files), encoding="utf-8")
    snapshot_state = "none" if args.write else _snapshot(state["peer"], workspace, before_files, turn_dir)
    pending = {"attempt": attempt, "dir": turn_dir.name, "write": bool(args.write), "phase": args.phase,
               "before": before, "replay": replay, "started": st.now_iso(), "snapshot": snapshot_state,
               "replan_seq": state.get("replan_seq", 0)}
    # A host edit made before this turn is recorded as the host's now, not folded into the peer's turn.
    base.update(run, lambda fresh: {"pending_turn": pending,
                                    **authors.observed(fresh, messages, before, fresh["host"])})
    return {"workspace": workspace, "before_files": before_files, "session": session, "delta_held": held,
            "prompt": prompt, "turn_dir": turn_dir, "pending": pending, "replay": replay}


def _verify_record(run: Path) -> None:
    """The record must still match its seal before the peer's turn is written."""
    check = getattr(st, "verify_after_turn", None) or st.verify_integrity
    check(run)


def _record(run: Path, args: argparse.Namespace, state: st.State, messages: List[st.Message],
            ready: Dict[str, Any], result: peers.PeerResult) -> int:
    """Write a completed turn (reply or failure) into the conversation and the state."""
    pending, replay, session = ready["pending"], ready["replay"], ready["session"]
    violation, after, after_files = _close_files(state, pending, ready["workspace"], ready["turn_dir"],
                                                 ready["before_files"])
    primed = bool(state.get("peer_primed", state["peer_session"] is not None)) and not replay
    if not result.ok:
        note = _failed_turn_note(ready["before_files"], after_files, violation)
        # A session that never completed a turn may not hold the rules and the goal: start over next time.
        kept = (result.session or session) if primed else None
        st.append_message(run, "system", args.phase, f"Peer turn failed: {result.error}{note}", kind="peer_error",
                          meta={"usage": result.usage or {}}, change=lambda fresh, seq: {
            "pending_turn": None, "peer_session": kept, "last_peer_failed": primed,
            "peer_replay": replay, "peer_turns": fresh["peer_turns"] + 1,
            **_usage_change(fresh, result.session or session, result.usage),
            **models.seen_change(result.model),
            **_authorship(pending, after, messages, fresh)})
        if violation:
            st.append_message(run, "system", args.phase, violation, kind="readonly_violation")
        warn(result.error + note)
        return EXIT_PEER_FAILED
    broke_read_only = bool(violation)
    fresh = st.load_state(run)
    current_phase = phases.current(fresh, st.read_messages(run))
    superseded = (fresh.get("replan_seq", 0) != pending.get("replan_seq", 0)
                  or current_phase not in (phases.current(state, messages), args.phase))
    verdict = gates.parse_verdict(result.reply)
    ignored = verdict if ((args.write or broke_read_only or superseded) and verdict == "approve") else None
    meta = {"verdict": None if ignored else verdict, "verdict_ignored": ignored,
            "observed_seq": messages[-1]["seq"] if messages else 0,
            "write": args.write or broke_read_only, "fingerprint": after, "usage": result.usage or {}}
    st.append_message(run, state["peer"], args.phase, result.reply, meta=meta, change=lambda fresh, seq: {
        "pending_turn": None, "peer_session": result.session, "peer_replay": False,
        "peer_cursor": messages[-1]["seq"] if messages else 0, "withheld": ready["delta_held"],
        "peer_turns": fresh["peer_turns"] + 1, **_usage_change(fresh, result.session, result.usage),
        **models.seen_change(result.model),
        "last_peer_failed": False, "peer_primed": True, "phase": current_phase if superseded else args.phase,
        **_authorship(pending, after, messages, fresh)})
    if args.phase == "propose":
        st.reveal_sealed(run, state["host"])
    emit(result.reply)
    if broke_read_only:
        st.append_message(run, "system", args.phase, violation, kind="readonly_violation")
        warn(violation)
        return EXIT_READONLY_BROKEN
    if ignored:
        warn("the peer's approval is not counted: the turn could edit files or its plan was superseded")
    return EXIT_OK


def _admit(run: Path, args: argparse.Namespace, state: st.State) -> Tuple[st.State, List[st.Message], Dict[str, Any]]:
    """Check the current gates and publish a pending turn before releasing the state lock."""
    messages = st.read_messages(run)
    problem = peers.launch_problem(state["peer"]) or auth.problem(state["peer"], auth.mode_of(state), os.environ)
    if problem:
        raise st.DuetError(problem)
    blocker = _peer_blocker(state, messages, args.phase, args.write, args.replan)
    if blocker:
        return state, messages, {"blocker": blocker}
    here = phases.current(state, messages)
    if args.replan and args.phase != here:
        # Going back to plan is the host's decision, so it stands whether or not this turn completes.
        record_replan(run, here, args.replan)
        state = st.load_state(run)
        messages = st.read_messages(run)
    ready = _prepare(run, args, state, messages)
    return state, messages, ready


def _run(run: Path, args: argparse.Namespace) -> int:
    with base.transaction(run) as fresh:
        state, messages, ready = _admit(run, args, fresh)
    if "blocker" in ready:
        code, text = ready["blocker"]
        warn(text)
        return code
    state = st.updated(state, pending_turn=ready["pending"])
    try:
        with _stoppable():
            result = peers.run_turn(
                state["peer"], ready["prompt"], ready["workspace"], ready["turn_dir"], ready["session"],
                args.write, state["peer_model"], args.timeout, state.get("peer_effort"),
                bool(state.get("peer_user_config")), store.hidden_from_peer(run), auth.mode_of(state),
            )
            with base.transaction(run):
                _verify_record(run)
                return _record(run, args, state, messages, ready, result)
    except st.TamperError:
        # The record cannot be trusted or written; the workspace still gets a read-only turn undone.
        with _unstoppable():
            _close_files(state, ready["pending"], ready["workspace"], ready["turn_dir"], ready["before_files"])
        raise
    except Stopped as exc:
        with _unstoppable():
            why = STOPPED_BY_SIGNAL.format(signum=exc.signum)
            warn(settle_pending(run, why) or why)
        return EXIT_PEER_FAILED
    except BaseException as exc:
        with _unstoppable():
            settle_pending(run, f"the runner hit an internal error ({type(exc).__name__}: {exc})")
        raise


def cmd_peer(args: argparse.Namespace) -> int:
    run = Path(args.run)
    st.load_state(run)  # a folder that is not a run is reported as such, before any lock file is made in it
    with base.peer_lock(run) as mine:
        if not mine:
            warn(base.BUSY_TEXT)
            return EXIT_USAGE
        return _run(run, args)


def settle_if_abandoned(run: Path) -> None:
    """At the start of a command: record a turn whose runner died, unless that turn is still running."""
    if not st.load_state(run).get("pending_turn"):
        return
    with base.peer_lock(run) as mine:
        if mine:
            note = settle_pending(run, STOPPED_UNSEEN)
            if note:
                warn(note)


def turn_standing(run: Path, state: st.State) -> Optional[str]:
    """For `status`: whether a peer turn is in flight or was left unrecorded."""
    if not state.get("pending_turn"):
        return None
    with base.peer_lock(run) as mine:
        return "interrupted; the next command records it" if mine else "running"
