"""Command-line entry for the duet runner. Each command does one step of the shared conversation."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import gates, peers, phases, prompts, snapshot, store
from . import state as st

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
READONLY_TEXT = "Files in the workspace changed during a read-only turn by {peer}: {names}. "
READONLY_RESTORED = ("They were put back exactly as they were; what the peer did is saved in {evidence}. "
                     "Any approval in that turn is not counted.")
READONLY_LEFT = ("{why}, so the change is still in place; a listing is saved in {evidence}. "
                 "Inspect it before continuing; any approval in that turn is not counted.")
VIOLATION_DIR = "readonly-violation"
NAMES_SHOWN = 10
BLIND_PHASES = ("frame", "propose")  # the host's sealed proposal is not released in these
FINISHED_TEXT = "this run is finished; start a new one"
TURN_CHANGED = (" Workspace files changed during the turn ({names}), so its work may be complete on disk: "
                "run `check` before deciding what to do next.")
TURN_UNCHANGED = " No workspace file changed during the turn."


def emit(text: str) -> None:
    sys.stdout.write(text.rstrip("\n") + "\n")


def warn(text: str) -> None:
    sys.stderr.write(f"duet: {text}\n")


def _read_text(path: Optional[str]) -> str:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    if not text.strip():
        raise st.DuetError("the message is empty")
    return text


def _goal(messages: List[st.Message]) -> str:
    return next((m["text"] for m in messages if m["kind"] == "goal"), "")


def cmd_init(args: argparse.Namespace) -> int:
    if bool(args.accept) == bool(args.waive_accept):
        warn("give exactly one of --accept '<command>' or --waive-accept '<reason>'")
        return EXIT_USAGE
    if args.max_turns < 1:
        raise st.DuetError("--max-turns must be at least 1")
    if args.max_cost is not None and args.max_cost <= 0:
        raise st.DuetError("--max-cost must be greater than 0")
    problem = peers.launch_problem(args.peer)
    if problem:
        raise st.DuetError(problem)
    run = st.new_run(
        Path(args.workspace), _read_text(args.goal_file), args.peer,
        args.accept, args.waive_accept, args.max_turns, args.peer_model, args.peer_effort,
        args.peer_user_config, args.max_cost, args.skip_proposals,
        gates.fingerprint(Path(args.workspace)) if Path(args.workspace).is_dir() else None,
    )
    own_key = {"codex": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}[args.peer]
    if os.environ.get(own_key):
        warn(f"{own_key} is set in this environment: the {args.peer} client may bill that API key "
             "instead of the subscription plan")
    if st.load_state(run)["store"] == store.STORE_WORKSPACE:
        warn(store.FALLBACK_NOTICE.format(home=store.duet_home(), root=st.RUN_ROOT))
    emit(str(run))
    return EXIT_OK


def cmd_say(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    speaker = args.speaker or state["host"]
    if speaker not in (state["host"], "user"):
        raise st.DuetError(f"--speaker must be '{state['host']}' or 'user'; the peer speaks through `peer`")
    if args.verdict and speaker != state["host"]:
        raise st.DuetError("only the host model records a verdict with `say`")
    messages = st.read_messages(run)
    here = phases.current(state, messages)
    if speaker == "user":  # the user may speak at any point; their words never move the run
        if args.replan is not None:
            raise st.DuetError("--replan belongs on the host model's own message or on a peer turn")
        phase = here
    else:
        problem = _order_problem(state, messages, args.phase, args.replan)
        if problem:
            warn(problem[1])
            return problem[0]
        phase = args.phase
    text = _read_text(args.file)
    current = gates.fingerprint(Path(state["workspace"]))
    meta: Dict[str, Any] = {"verdict": args.verdict, "fingerprint": current} if args.verdict else {}
    peer_proposed = any(m["speaker"] == state["peer"] and m["phase"] == "propose" for m in messages)
    sealed = speaker == state["host"] and phase == "propose" and not peer_proposed
    if phase != here and args.replan:
        _record_replan(run, here, args.replan)
    message = st.append_message(run, speaker, phase, text, meta=meta, sealed=sealed)
    if sealed:
        _discard_draft(run, args.file)
    changes: Dict[str, Any] = {**_observed(state, current, state["host"]), "phase": phase}
    if speaker == "user":
        changes["fail_streak"] = {"digest": None, "count": 0}
    st.save_state(run, st.updated(st.load_state(run), **changes))
    emit(f"recorded message {message['seq']} from {speaker}" + (" (sealed until the peer proposes)" if sealed else ""))
    return EXIT_OK


def _order_problem(
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
    problem = phases.refusal(state, messages, phase, replan)
    if problem:
        allowed = ", ".join(phases.allowed_next(state, messages))
        return EXIT_ORDER, f"{problem}. Allowed now: {allowed}"
    return None


def _record_replan(run: Path, here: str, reason: str) -> None:
    st.append_message(run, "system", phases.PLAN, f"Back to plan from {here}: {reason.strip()}",
                      kind=phases.REPLAN_KIND)


def _failed_turn_note(before: Dict[str, str], after: Dict[str, str], violation: str) -> str:
    """What a failed peer turn left on disk; a read-only turn's change is reported by its own message."""
    if violation:
        return ""
    changed = gates.changed_files(before, after)
    if not changed:
        return TURN_UNCHANGED
    extra = len(changed) - NAMES_SHOWN
    return TURN_CHANGED.format(names=", ".join(changed[:NAMES_SHOWN]) + (f" and {extra} more" if extra > 0 else ""))


def _observed(state: st.State, current: str, actor: str) -> Dict[str, Any]:
    """State changes that record who the files' last author is, given what is on disk now."""
    if state.get("seen_fingerprint") in (None, current):
        return {"seen_fingerprint": current}
    return {"seen_fingerprint": current, "last_writer": actor}


def _discard_draft(run: Path, source: Optional[str]) -> None:
    """Remove the plain-text draft of a sealed proposal when it lives in the run's drafts folder."""
    if not source:
        return
    path = Path(source).resolve()
    if (run.resolve() / "drafts") in path.parents:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


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
                          "continue only if they say so, with `extend`")
    if gates.is_stalled(state, messages):
        return EXIT_STALLED, (f"the acceptance command failed {gates.STALL_AFTER} times the same way. "
                              "Stop and ask the user; record their answer with `say --speaker user`")
    cap, spent = state.get("max_cost_usd"), state.get("peer_usage", {}).get("cost_usd", 0.0)
    if cap is not None and spent >= cap:
        return EXIT_CAP, (f"cost cap reached (${spent:.2f} of ${cap:.2f} reported by the peer client). "
                          "Stop and report to the user")
    out_of_order = _order_problem(state, messages, phase, replan, write)
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


def _add_usage(total: Optional[Dict[str, Any]], turn: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    keys = ("input_tokens", "output_tokens", "cost_usd")
    return {key: (total or {}).get(key, 0) + (turn or {}).get(key, 0) for key in keys}


def _snapshot(workspace: Path, files: Dict[str, str], turn_dir: Path) -> Optional[Path]:
    """Copy the workspace's source files before a read-only turn; None when it is too large to copy."""
    dest = turn_dir / "snapshot"
    if snapshot.take(workspace, files, dest):
        return dest
    warn(f"the workspace is over the snapshot ceiling ({snapshot.max_bytes() // (1024 * 1024)} MB, "
         f"{snapshot.MAX_ENV}) or could not be copied: a change in this read-only turn will be reported "
         "but not undone")
    return None


def _undo_read_only_change(
    state: st.State, workspace: Path, turn_dir: Path, snap: Optional[Path],
    before_files: Dict[str, str], after_files: Dict[str, str],
) -> Tuple[str, str]:
    """Save what the peer changed, put the files back, and return (what to record, fingerprint now)."""
    changed = gates.changed_files(before_files, after_files)
    evidence = turn_dir / VIOLATION_DIR
    snapshot.save_evidence(workspace, snap, changed, before_files, after_files, evidence)
    extra = len(changed) - NAMES_SHOWN
    names = ", ".join(changed[:NAMES_SHOWN]) + (f" and {extra} more" if extra > 0 else "")
    head = READONLY_TEXT.format(peer=state["peer"], names=names)
    if snap is None:
        why = "No snapshot was taken (workspace over the size ceiling)"
        return head + READONLY_LEFT.format(why=why, evidence=evidence), gates.digest_of(after_files)
    failed = snapshot.restore(workspace, snap, changed, before_files)
    now = gates.fingerprint(workspace)
    if failed or now != gates.digest_of(before_files):
        why = "Restoring failed for " + (", ".join(failed[:NAMES_SHOWN]) or "some files")
        return head + READONLY_LEFT.format(why=why, evidence=evidence), now
    return head + READONLY_RESTORED.format(evidence=evidence), now


def cmd_peer(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    messages = st.read_messages(run)
    problem = peers.launch_problem(state["peer"])
    if problem:
        raise st.DuetError(problem)
    blocker = _peer_blocker(state, messages, args.phase, args.write, args.replan)
    if blocker:
        warn(blocker[1])
        return blocker[0]
    here = phases.current(state, messages)
    if args.replan and args.phase != here:
        # Going back to plan is the host's decision, so it stands whether or not this turn completes.
        _record_replan(run, here, args.replan)
        state = st.updated(st.load_state(run), phase=args.phase)
        st.save_state(run, state)
        messages = st.read_messages(run)
    workspace = Path(state["workspace"])
    before_files = gates.manifest(workspace)
    before = gates.digest_of(before_files)
    authorship = _observed(state, before, state["host"])
    session = None if args.new_session else state["peer_session"]
    if args.new_session:
        # Rebuild the peer's memory from the record: everything said so far, its own words included.
        hidden = set(_delta(state, messages, args.phase)[1])
        delta, held = [m for m in messages if m["seq"] not in hidden and m["kind"] != "goal"], sorted(hidden)
    else:
        delta, held = _delta(state, messages, args.phase)
    changes = gates.change_summary(workspace) if args.phase == "review" else ""
    plan_mode = state["peer"] == "claude" and peers.claude_uses_plan_mode(args.write)
    prompt = prompts.build_prompt(state, _goal(messages), args.phase, delta, args.write, session is None,
                                  retry=bool(state.get("last_peer_failed")) and session is not None,
                                  changes=changes, plan_mode=plan_mode)
    attempt = len(list((run / "turns").iterdir())) + 1
    turn_dir = run / "turns" / f"{attempt:03d}"
    snap = None if args.write else _snapshot(workspace, before_files, turn_dir)
    result = peers.run_turn(
        state["peer"], prompt, workspace, turn_dir,
        session, args.write, state["peer_model"], args.timeout, state.get("peer_effort"),
        bool(state.get("peer_user_config")), store.hidden_from_peer(run),
    )
    after_files = gates.manifest(workspace)
    after = gates.digest_of(after_files)
    violation = ""
    if not args.write and after != before:
        violation, after = _undo_read_only_change(state, workspace, turn_dir, snap, before_files, after_files)
    snapshot.discard(snap)
    if after != before:
        authorship = {"seen_fingerprint": after, "last_writer": state["peer"]}
    primed = state.get("peer_primed", state["peer_session"] is not None) and not args.new_session
    if not result.ok:
        note = _failed_turn_note(before_files, after_files, violation)
        st.append_message(run, "system", args.phase, f"Peer turn failed: {result.error}{note}", kind="peer_error")
        if violation:
            st.append_message(run, "system", args.phase, violation, kind="readonly_violation")
        fresh = st.load_state(run)
        # A session that never completed a turn may not hold the rules and the goal: start over next time.
        kept = (result.session or session) if primed else None
        st.save_state(run, st.updated(fresh, peer_session=kept, last_peer_failed=primed,
                                      peer_turns=fresh["peer_turns"] + 1, **authorship))
        warn(result.error + note)
        return EXIT_PEER_FAILED
    broke_read_only = bool(violation)
    verdict = gates.parse_verdict(result.reply)
    ignored = verdict if ((args.write or broke_read_only) and verdict == "approve") else None
    meta = {
        "verdict": None if ignored else verdict,
        "verdict_ignored": ignored,
        "write": args.write or broke_read_only,
        "fingerprint": after,
        "usage": result.usage or {},
    }
    st.append_message(run, state["peer"], args.phase, result.reply, meta=meta)
    if args.phase == "propose":
        st.reveal_sealed(run, state["host"])
    fresh = st.load_state(run)  # the turn can take a long time; do not overwrite changes made meanwhile
    st.save_state(run, st.updated(
        fresh, peer_session=result.session, peer_cursor=messages[-1]["seq"] if messages else 0,
        withheld=held, peer_turns=fresh["peer_turns"] + 1,
        peer_usage=_add_usage(fresh.get("peer_usage"), result.usage), last_peer_failed=False, peer_primed=True,
        phase=args.phase, **authorship,
    ))
    emit(result.reply)
    if broke_read_only:
        st.append_message(run, "system", args.phase, violation, kind="readonly_violation")
        warn(violation)
        return EXIT_READONLY_BROKEN
    if ignored:
        warn("the peer approved in a turn where it could edit files; that approval is not counted")
    return EXIT_OK


def _phase_now(run: Path, state: st.State) -> str:
    """The run's current phase, used to label what the runner itself records."""
    return phases.current(state, st.read_messages(run))


def cmd_check(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if not state.get("accept_cmd"):
        raise st.DuetError("no acceptance command is set; use `accept --cmd`")
    authorship = _observed(state, gates.fingerprint(Path(state["workspace"])), state["host"])
    result = gates.run_check(state, args.timeout)
    outcome = "PASSED" if result["passed"] else f"FAILED (exit {result['exit_code']})"
    number = len(list((run / "checks").glob("*.log"))) + 1 if (run / "checks").is_dir() else 0
    log_note = ""
    if number:
        log = run / "checks" / f"{number:03d}.log"
        log.write_text(result["full_output"], encoding="utf-8")
        log_note = f"\n\nFull output: {log}"
    text = f"Acceptance `{state['accept_cmd']}` {outcome}.\n\n```\n{result['output']}\n```{log_note}"
    if not result["stable"]:
        names = ", ".join(result["changed_during"][:10])
        text += (f"\n\nThe command itself changed files ({names}), so this result cannot close the run. "
                 "Add its by-products to .gitignore or stop it writing them, then run check again.")
    if result["passed"] and result["fingerprint"] == state.get("init_fingerprint"):
        text += ("\n\nNote: this passed with no file changed since the run started, "
                 "so the command may not actually test the goal.")
    message = st.append_message(run, "system", _phase_now(run, state), text, kind="check",
                                meta={"passed": result["passed"], "exit_code": result["exit_code"]})
    st.save_state(run, st.updated(
        state,
        last_check={"seq": message["seq"], "passed": result["passed"], "stable": result["stable"],
                    "fingerprint": result["fingerprint"]},
        fail_streak=gates.next_streak(state["fail_streak"], result),
        **{**authorship, "seen_fingerprint": result["fingerprint"]},
    ))
    emit(text)
    return EXIT_OK if result["passed"] else EXIT_CHECK_FAILED


def cmd_experiment(args: argparse.Namespace) -> int:
    """Run a command both models agreed would settle a disagreement, and put the output on the record."""
    run = Path(args.run)
    state = st.load_state(run)
    if not args.cmd.strip():
        raise st.DuetError("the experiment command is empty")
    code, output = gates.run_command(args.cmd, Path(state["workspace"]), args.timeout)
    text = f"Experiment `{args.cmd}` exited {code}.\n\n```\n{gates.excerpt(output)}\n```"
    st.append_message(run, "system", _phase_now(run, state), text, kind="experiment", meta={"exit_code": code})
    emit(text)
    return EXIT_OK if code == 0 else EXIT_CHECK_FAILED


def cmd_accept(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if not args.cmd.strip():
        raise st.DuetError("the acceptance command is empty")
    here = _phase_now(run, state)
    if here != phases.FRAME and not args.by_user:
        raise st.DuetError("the acceptance command is locked once the frame phase is over. Changing it now "
                           "needs the user's agreement: pass --by-user \"<their words>\"")
    st.save_state(run, st.updated(state, accept_cmd=args.cmd, accept_waived=None, last_check=None,
                                  fail_streak={"digest": None, "count": 0}))
    note = f" Changed with the user's agreement: {args.by_user}" if args.by_user else ""
    st.append_message(run, "system", here, f"Acceptance command set to `{args.cmd}`.{note}", kind="accept")
    emit("acceptance command recorded; earlier check results no longer count")
    return EXIT_OK


def cmd_extend(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if args.turns < 1:
        raise st.DuetError("--turns must be at least 1")
    new_cap = state["max_turns"] + args.turns
    st.save_state(run, st.updated(state, max_turns=new_cap))
    st.append_message(run, "system", _phase_now(run, state), f"Turn cap raised to {new_cap}: {args.reason}",
                      kind="extend")
    emit(f"turn cap is now {new_cap}")
    return EXIT_OK


def cmd_reseal(args: argparse.Namespace) -> int:
    """Accept an in-workspace run's record as it stands after the integrity check refused it."""
    run = Path(args.run)
    state = st.load_state(run)
    st.reseal(run)
    st.append_message(run, "system", _phase_now(run, state),
                      f"Integrity record reset with the user's agreement: {args.by_user}", kind="reseal")
    emit("integrity record reset; the run's state and messages are trusted as they stand now")
    return EXIT_OK


def _report(run: Path) -> Dict[str, Any]:
    state = st.load_state(run)
    messages = st.read_messages(run)
    ready, blocking = gates.finish_report(state, messages)
    current = gates.fingerprint(Path(state["workspace"]))
    check = state["last_check"] or {}
    verdicts = {}
    for speaker in (state["host"], state["peer"]):
        latest = gates.review_standing(messages, speaker, 0)[0]
        verdicts[speaker] = (latest["meta"].get("verdict") if latest else None)
    done = state["status"] != "active"
    return {
        "run": str(run), "store": state.get("store", store.STORE_WORKSPACE), "status": state["status"],
        "phase": "done" if done else phases.current(state, messages),
        "allowed_next": [] if done else phases.allowed_next(state, messages),
        "with_replan": [] if done else phases.needs_replan(state, messages),
        "host": state["host"], "peer": state["peer"],
        "peer_session": state["peer_session"], "peer_turns": state["peer_turns"],
        "max_turns": state["max_turns"], "messages": len(messages),
        "peer_usage": state.get("peer_usage"), "max_cost_usd": state.get("max_cost_usd"),
        "accept_cmd": state["accept_cmd"], "accept_waived": state["accept_waived"],
        "last_check_passed": check.get("passed"),
        "check_valid_on_current_files": bool(check.get("passed") and check.get("stable", True)
                                             and check.get("fingerprint") == current),
        "review_verdicts": verdicts,
        "stalled": gates.is_stalled(state, messages),
        "last_writer": gates.current_writer(state, current),
        "proposals_recorded": gates.both_proposed(state, messages),
        "ready_to_finish": ready, "blocking": blocking,
        "transcript": str(run / st.TRANSCRIPT_FILE),
    }


def cmd_status(args: argparse.Namespace) -> int:
    emit(json.dumps(_report(Path(args.run)), indent=2))
    return EXIT_OK


def cmd_finish(args: argparse.Namespace) -> int:
    run = Path(args.run)
    report = _report(run)
    if report["status"] == "done":
        emit("already finished")
        return EXIT_OK
    if not report["ready_to_finish"]:
        emit("Not finished. Still blocking:\n" + "\n".join(f"- {reason}" for reason in report["blocking"]))
        return EXIT_NOT_DONE
    state = st.load_state(run)
    summary = _finish_summary(report)
    st.append_message(run, "system", _phase_now(run, state), f"Run finished: {summary}.", kind="finish")
    st.save_state(run, st.updated(state, status="done"))
    emit(f"Finished: {summary}. Transcript: {report['transcript']}")
    return EXIT_OK


def _finish_summary(report: Dict[str, Any]) -> str:
    """Who wrote the final change and who approved it: only the non-author's approval is needed."""
    proof = (f"acceptance was waived ({report['accept_waived']})" if report["accept_waived"]
             else f"acceptance `{report['accept_cmd']}` passed on the current files")
    writer = report["last_writer"]
    if writer is None:
        return (f"{proof}; no file was changed during the run, and {report['host']} and {report['peer']} "
                "approved the files as they stand")
    approver = report["peer"] if writer == report["host"] else report["host"]
    return f"{proof}; {writer} wrote the final change and {approver} approved it"


PHASE_HELP = "one of " + ", ".join(st.PHASES) + ", in that order (build and review alternate)"
REPLAN_HELP = "go back to plan from build or review, saying what invalidated the plan"


def _phase_arg(value: str) -> str:
    try:
        return phases.normalise(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="duet", description="Two models, one conversation, one build.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="start a run for a workspace")
    init.add_argument("--workspace", required=True)
    init.add_argument("--goal-file", help="file holding the goal; stdin if omitted")
    init.add_argument("--accept", help="command that must exit 0 for the goal to count as met")
    init.add_argument("--waive-accept", help="reason there is no runnable acceptance command")
    init.add_argument("--peer", default="codex", choices=sorted(st.HOST_FOR_PEER))
    init.add_argument("--peer-model", help="override the peer client's own default model")
    init.add_argument("--peer-effort", help="Codex reasoning effort for this run; its own default if omitted")
    init.add_argument("--peer-user-config", action="store_true",
                      help="Claude peer only: load your personal Claude setup (hooks, instructions, plugins) "
                           "instead of a clean session")
    init.add_argument("--skip-proposals", metavar="REASON",
                      help="allow building and finishing without the blind proposal round (say why)")
    init.add_argument("--max-cost", type=float,
                      help="stop asking the peer once its client has reported this many US dollars "
                           "(the Claude client reports cost; Codex reports tokens only)")
    init.add_argument("--max-turns", type=int, default=st.DEFAULT_MAX_TURNS)
    init.set_defaults(func=cmd_init)

    say = sub.add_parser("say", help="add the host model's or the user's message")
    say.add_argument("--run", required=True)
    say.add_argument("--phase", type=_phase_arg, metavar="PHASE", help=PHASE_HELP + "; not needed with --speaker user")
    say.add_argument("--replan", metavar="REASON", help=REPLAN_HELP)
    say.add_argument("--speaker")
    say.add_argument("--file", help="file holding the message; stdin if omitted")
    say.add_argument("--verdict", choices=("approve", "revise"))
    say.set_defaults(func=cmd_say)

    peer = sub.add_parser("peer", help="give the peer everything new and record its reply")
    peer.add_argument("--run", required=True)
    peer.add_argument("--phase", required=True, type=_phase_arg, metavar="PHASE", help=PHASE_HELP)
    peer.add_argument("--replan", metavar="REASON", help=REPLAN_HELP)
    peer.add_argument("--write", action="store_true", help="let the peer edit the workspace this turn (build only)")
    peer.add_argument("--new-session", action="store_true",
                      help="start a fresh peer session and replay the whole conversation into it "
                           "(use when the old session cannot be resumed)")
    peer.add_argument("--timeout", type=int, default=peers.DEFAULT_TIMEOUT_SECONDS)
    peer.set_defaults(func=cmd_peer)

    check = sub.add_parser("check", help="run the acceptance command and record the result")
    check.add_argument("--run", required=True)
    check.add_argument("--timeout", type=int, default=gates.CHECK_TIMEOUT_SECONDS)
    check.set_defaults(func=cmd_check)

    accept = sub.add_parser("accept", help="set or replace the acceptance command")
    accept.add_argument("--run", required=True)
    accept.add_argument("--cmd", required=True)
    accept.add_argument("--by-user", metavar="WORDS", help="the user's agreement, required after the frame phase")
    accept.set_defaults(func=cmd_accept)

    experiment = sub.add_parser("experiment", help="run a command to settle a disagreement and record its output")
    experiment.add_argument("--run", required=True)
    experiment.add_argument("--cmd", required=True)
    experiment.add_argument("--timeout", type=int, default=600)
    experiment.set_defaults(func=cmd_experiment)

    extend = sub.add_parser("extend", help="raise the turn cap after the user agrees")
    extend.add_argument("--run", required=True)
    extend.add_argument("--turns", type=int, required=True)
    extend.add_argument("--reason", required=True)
    extend.set_defaults(func=cmd_extend)

    reseal = sub.add_parser("reseal", help="accept an in-workspace run's record after exit 9 (the user's call)")
    reseal.add_argument("--run", required=True)
    reseal.add_argument("--by-user", metavar="WORDS", required=True, help="the user's agreement, in their words")
    reseal.set_defaults(func=cmd_reseal)

    for name, func, text in (("status", cmd_status, "show where the run stands"),
                             ("finish", cmd_finish, "close the run if every gate holds")):
        command = sub.add_parser(name, help=text)
        command.add_argument("--run", required=True)
        command.set_defaults(func=func)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    try:
        if getattr(args, "run", None) and args.command != "reseal":
            notice = st.verify_integrity(Path(args.run))
            if notice:
                warn(notice)
        return args.func(args)
    except st.TamperError as exc:
        warn(str(exc))
        return EXIT_TAMPERED
    except (st.DuetError, OSError) as exc:
        warn(str(exc))
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - an unexpected fault must not look like a failed check (exit 1)
        warn(f"internal error ({type(exc).__name__}): {exc}")
        return EXIT_INTERNAL
