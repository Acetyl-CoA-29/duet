"""Command-line entry for the duet runner. Each command does one step of the shared conversation."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import authors, base, gates, peers, phases, store, turn
from . import state as st
from .base import (EXIT_BLIND, EXIT_CAP, EXIT_CHECK_FAILED, EXIT_INTERNAL, EXIT_NOT_DONE,  # noqa: F401 - re-exported
                   EXIT_OK, EXIT_ORDER, EXIT_PEER_FAILED, EXIT_READONLY_BROKEN, EXIT_STALLED, EXIT_TAMPERED,
                   EXIT_USAGE, FINISHED_TEXT, emit, warn)
from .turn import cmd_peer

FROZEN_WHEN_DONE = ("say", "peer", "check", "accept", "extend", "experiment")  # commands a finished run refuses
READ_ONLY_COMMANDS = ("status", "reseal")  # these never settle an abandoned peer turn
FINISH_PHASES = (phases.BUILD, phases.REVIEW)
USAGE_NOTE = {
    "codex": ("tokens the Codex client reported for this run's peer turns. Codex reports a running total per "
              "session, so the runner adds only the increase each turn; input_tokens includes cached input. "
              "Codex reports no cost, so cost_usd stays 0"),
    "claude": ("tokens and US dollars the Claude client reported, summed over this run's peer turns; each "
               "report covers one turn"),
}


def _read_text(path: Optional[str]) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8-sig") if path else sys.stdin.read()
    except UnicodeDecodeError as exc:
        raise st.DuetError(f"{path} is not UTF-8 text ({exc.reason} at byte {exc.start}); save it as UTF-8") from exc
    if not text.strip():
        raise st.DuetError("the message is empty")
    return text


def _dollars(value: Optional[float], flag: str) -> None:
    if value is not None and not (math.isfinite(value) and value > 0):
        raise st.DuetError(f"{flag} must be a number of dollars greater than 0")


def cmd_init(args: argparse.Namespace) -> int:
    if sum(bool(choice) for choice in (args.accept, args.waive_accept, args.accept_later)) != 1:
        warn("give exactly one of --accept '<command>', --waive-accept '<reason>' or --accept-later "
             "(the frame phase must then set the command)")
        return EXIT_USAGE
    if args.max_turns < 1:
        raise st.DuetError("--max-turns must be at least 1")
    _dollars(args.max_cost, "--max-cost")
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
    peer_proposed = any(m["speaker"] == state["peer"] and m["phase"] == "propose" for m in messages)
    if speaker == "user":  # the user may speak at any point; their words never move the run
        if args.replan is not None:
            raise st.DuetError("--replan belongs on the host model's own message or on a peer turn")
        phase = here
    else:
        problem = turn.order_problem(state, messages, args.phase, args.replan)
        if problem:
            warn(problem[1])
            return problem[0]
        phase = args.phase
        if phase == phases.PROPOSE and peer_proposed:
            warn(f"{state['peer']} has already proposed, so the blind round is over: a second proposal from "
                 f"{state['host']} would be written with theirs in view. Revisions belong in plan")
            return EXIT_ORDER
    text = _read_text(args.file)
    current = gates.fingerprint(Path(state["workspace"]))
    meta: Dict[str, Any] = {"verdict": args.verdict, "fingerprint": current} if args.verdict else {}
    sealed = speaker == state["host"] and phase == phases.PROPOSE
    if phase != here and args.replan:
        turn.record_replan(run, here, args.replan)
    def change(fresh: st.State, _seq: int) -> Dict[str, Any]:
        seen = authors.observed(fresh, messages, current, fresh["host"])
        quiet = {"fail_streak": {"digest": None, "count": 0}} if speaker == "user" else {}
        return {**seen, **quiet, "phase": phase if speaker != "user" else fresh.get("phase", phase)}

    message = st.append_message(run, speaker, phase, text, meta=meta, sealed=sealed, change=change)
    if sealed:
        _discard_draft(run, args.file)
    emit(f"recorded message {message['seq']} from {speaker}" + (" (sealed until the peer proposes)" if sealed else ""))
    return EXIT_OK


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


def _phase_now(run: Path, state: st.State) -> str:
    """The run's current phase, used to label what the runner itself records."""
    return phases.current(state, st.read_messages(run))


def cmd_check(args: argparse.Namespace) -> int:
    run = Path(args.run)
    with base.transaction(run) as state:
        if not state.get("accept_cmd"):
            raise st.DuetError("no acceptance command is set; use `accept --cmd`")
        before = gates.manifest(Path(state["workspace"]))  # one scan serves authorship and the check's baseline
        found = gates.digest_of(before)
        messages = st.read_messages(run)
        state = base.update(run, lambda fresh: authors.observed(fresh, messages, found, fresh["host"]))
    result = gates.run_check(state, args.timeout, before=before)
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
    def change(fresh: st.State, seq: int) -> Dict[str, Any]:
        # Authorship was recorded at admission; comparing the old baseline with a later
        # peer receipt here would falsely attribute that peer's work to the host. The command's own
        # by-products become the files last seen, unless another command recorded a change meanwhile.
        seen = {"seen_fingerprint": result["fingerprint"]} if fresh.get("seen_fingerprint") == found else {}
        if fresh.get("accept_cmd") != state["accept_cmd"]:
            return seen  # the command was replaced while this one ran: its result proves nothing now
        return {**seen, "fail_streak": gates.next_streak(fresh["fail_streak"], result),
                "last_check": {"seq": seq, "passed": result["passed"], "stable": result["stable"],
                               "fingerprint": result["fingerprint"]}}

    with base.transaction(run) as fresh:
        st.append_message(run, "system", _phase_now(run, fresh), text, kind="check",
                          meta={"passed": result["passed"], "exit_code": result["exit_code"]}, change=change)
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
    with base.transaction(run) as fresh:
        st.append_message(run, "system", _phase_now(run, fresh), text, kind="experiment", meta={"exit_code": code})
    emit(text)
    return EXIT_OK if code == 0 else EXIT_CHECK_FAILED


def cmd_accept(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    command, waiver = (args.cmd or "").strip(), (args.waive or "").strip()
    if bool(command) == bool(waiver):
        raise st.DuetError("give exactly one of --cmd \"<command>\" or --waive \"<reason>\", and not empty")
    here = _phase_now(run, state)
    if here != phases.FRAME and not args.by_user:
        raise st.DuetError("the acceptance command is locked once the frame phase is over. Changing it now "
                           "needs the user's agreement: pass --by-user \"<their words>\"")
    note = f" Changed with the user's agreement: {args.by_user}" if args.by_user else ""
    what = f"Acceptance command set to `{args.cmd}`." if command else f"Acceptance waived: {waiver}."
    st.append_message(run, "system", here, what + note, kind="accept", change=lambda fresh, seq: {
        "accept_cmd": args.cmd if command else None, "accept_waived": waiver or None,
        "last_check": None, "fail_streak": {"digest": None, "count": 0}})
    emit("acceptance recorded; earlier check results no longer count")
    return EXIT_OK


def cmd_extend(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if args.turns is None and args.cost is None:
        raise st.DuetError("give --turns <n>, --cost <dollars> or both")
    if args.turns is not None and args.turns < 1:
        raise st.DuetError("--turns must be at least 1")
    _dollars(args.cost, "--cost")
    if args.cost is not None and state.get("max_cost_usd") is None:
        raise st.DuetError("this run has no cost cap to raise (it was started without --max-cost)")
    def change(fresh: st.State) -> Dict[str, Any]:
        cap = fresh["max_cost_usd"] + args.cost if args.cost is not None else fresh.get("max_cost_usd")
        _dollars(cap, "resulting cost cap")
        return {"max_turns": fresh["max_turns"] + (args.turns or 0), "max_cost_usd": cap}

    new = base.update(run, change)
    raised = ([f"turn cap raised to {new['max_turns']}"] if args.turns else []) + (
        [f"cost cap raised to ${new['max_cost_usd']:.2f}"] if args.cost is not None else [])
    summary = " and ".join(raised)
    st.append_message(run, "system", _phase_now(run, state), f"{summary[0].upper()}{summary[1:]}: {args.reason}",
                      kind="extend")
    emit(summary)
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
    current = gates.fingerprint(Path(state["workspace"]))
    done = state["status"] != "active"
    here = phases.current(state, messages)
    blocking = gates.finish_report(authors.gate_view(state, messages, current), messages, current)[1]
    running = turn.turn_standing(run, state)
    if not done and here not in FINISH_PHASES:
        blocking.append(f"the run is in {here}: it can finish only from build or review, once the plan is carried out")
    if not done and running:
        blocking.append(f"a peer turn is not recorded yet ({running})")
    check = state["last_check"] or {}
    verdicts = {}
    for speaker in authors.models(state):
        latest = gates.review_standing(messages, speaker, 0)[0]
        verdicts[speaker] = (latest["meta"].get("verdict") if latest else None)
    return {
        "run": str(run), "store": state.get("store", store.STORE_WORKSPACE), "status": state["status"],
        "phase": "done" if done else here,
        "allowed_next": [] if done else phases.allowed_next(state, messages),
        "with_replan": [] if done else phases.needs_replan(state, messages),
        "host": state["host"], "peer": state["peer"],
        "peer_session": state["peer_session"], "peer_turns": state["peer_turns"],
        "max_turns": state["max_turns"], "messages": len(messages), "peer_turn": running,
        "peer_usage": state.get("peer_usage"), "peer_usage_note": USAGE_NOTE[state["peer"]],
        "max_cost_usd": state.get("max_cost_usd"),
        "accept_cmd": state["accept_cmd"], "accept_waived": state["accept_waived"],
        "last_check_passed": check.get("passed"),
        "check_valid_on_current_files": bool(check.get("passed") and check.get("stable", True)
                                             and check.get("fingerprint") == current),
        "review_verdicts": verdicts,
        "stalled": gates.is_stalled(state, messages),
        "last_writer": gates.current_writer(state, current),
        "writers": authors.effective(state, messages, current),
        "proposals_recorded": gates.both_proposed(state, messages),
        "ready_to_finish": not blocking, "blocking": blocking,
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
    summary = _finish_summary(report)
    st.append_message(run, "system", report["phase"], f"Run finished: {summary}.", kind="finish",
                      change=lambda fresh, seq: {"status": "done"})
    emit(f"Finished: {summary}. Transcript: {report['transcript']}")
    return EXIT_OK


def _finish_summary(report: Dict[str, Any]) -> str:
    """Who changed the files and who approved them: a sole writer needs only the other model's approval."""
    proof = (f"acceptance was waived ({report['accept_waived']})" if report["accept_waived"]
             else f"acceptance `{report['accept_cmd']}` passed on the current files")
    who = report["writers"]
    if not who:
        return (f"{proof}; no file was changed during the run, and {report['host']} and {report['peer']} "
                "approved the files as they stand")
    if len(who) > 1:
        return (f"{proof}; {report['host']} and {report['peer']} both changed files since the last fully "
                "approved state, and both approved the files as they stand")
    approver = report["peer"] if who[0] == report["host"] else report["host"]
    return f"{proof}; {who[0]} wrote the final change and {approver} approved it"


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
    init.add_argument("--accept-later", action="store_true",
                      help="the acceptance command is not known yet: the frame phase must set it "
                           "(`accept --cmd`) before the run can move on")
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
    accept.add_argument("--cmd")
    accept.add_argument("--waive", metavar="REASON", help="record that no command can prove the goal, and why")
    accept.add_argument("--by-user", metavar="WORDS", help="the user's agreement, required after the frame phase")
    accept.set_defaults(func=cmd_accept)

    experiment = sub.add_parser("experiment", help="run a command to settle a disagreement and record its output")
    experiment.add_argument("--run", required=True)
    experiment.add_argument("--cmd", required=True)
    experiment.add_argument("--timeout", type=int, default=600)
    experiment.set_defaults(func=cmd_experiment)

    extend = sub.add_parser("extend", help="raise the turn cap or the cost cap after the user agrees")
    extend.add_argument("--run", required=True)
    extend.add_argument("--turns", type=int, help="peer turns to add to the cap")
    extend.add_argument("--cost", type=float, metavar="DOLLARS", help="dollars to add to the cost cap")
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


def _before(args: argparse.Namespace) -> Optional[int]:
    """What every command on an existing run does first; an exit code when the command must not go on."""
    if not getattr(args, "run", None) or args.command == "reseal":
        return None
    run = Path(args.run)
    notice = st.verify_integrity(run)
    if notice:
        warn(notice)
    st.reconcile(run)
    if args.command in READ_ONLY_COMMANDS:
        return None
    if st.load_state(run)["status"] != "active":
        if args.command in FROZEN_WHEN_DONE:
            warn(FINISHED_TEXT)
            return EXIT_USAGE
        return None
    turn.settle_if_abandoned(run)
    return None


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    try:
        refusal = _before(args)
        if refusal is not None:
            return refusal
        if getattr(args, "run", None) and args.command not in ("peer", "check", "experiment", "reseal"):
            with base.transaction(Path(args.run), active=args.command in FROZEN_WHEN_DONE):
                return args.func(args)
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
