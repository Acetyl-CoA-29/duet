"""Command-line entry for the duet runner. Each command does one step of the shared conversation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import gates, peers, prompts
from . import state as st

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_USAGE = 2
EXIT_CAP = 3
EXIT_STALLED = 4
EXIT_PEER_FAILED = 5
EXIT_BLIND = 6
EXIT_NOT_DONE = 7
EXIT_READONLY_BROKEN = 8
BLIND_PHASES = ("frame", "propose")  # the host's sealed proposal is not released in these


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
    text = _read_text(args.file)
    current = gates.fingerprint(Path(state["workspace"]))
    meta: Dict[str, Any] = {"verdict": args.verdict, "fingerprint": current} if args.verdict else {}
    messages = st.read_messages(run)
    peer_proposed = any(m["speaker"] == state["peer"] and m["phase"] == "propose" for m in messages)
    sealed = speaker == state["host"] and args.phase == "propose" and not peer_proposed
    message = st.append_message(run, speaker, args.phase, text, meta=meta, sealed=sealed)
    if sealed:
        _discard_draft(run, args.file)
    changes: Dict[str, Any] = _observed(state, current, state["host"])
    if speaker == "user":
        changes["fail_streak"] = {"digest": None, "count": 0}
    st.save_state(run, st.updated(st.load_state(run), **changes))
    emit(f"recorded message {message['seq']} from {speaker}" + (" (sealed until the peer proposes)" if sealed else ""))
    return EXIT_OK


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
    state: st.State, messages: List[st.Message], phase: str, write: bool = False,
) -> Optional[Tuple[int, str]]:
    if state["status"] != "active":
        return EXIT_USAGE, "this run is finished; start a new one"
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
    host_proposed = any(m["speaker"] == state["host"] and m["phase"] == "propose" for m in messages)
    peer_proposed = any(m["speaker"] == state["peer"] and m["phase"] == "propose" for m in messages)
    if phase == "propose" and not host_proposed:
        return EXIT_BLIND, (f"blind round: record {state['host']}'s own proposal first "
                            "(`say --phase propose`), then ask the peer")
    if phase == "debate" and not (host_proposed and peer_proposed):
        return EXIT_BLIND, "debate needs both blind proposals first: run the propose phase for both models"
    if write and gates.proposals_missing(state, messages):
        return EXIT_BLIND, ("no build turn before the blind proposal round: both models must record a "
                            "proposal first (or the run must have been started with --skip-proposals)")
    return None


def _add_usage(total: Optional[Dict[str, Any]], turn: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    keys = ("input_tokens", "output_tokens", "cost_usd")
    return {key: (total or {}).get(key, 0) + (turn or {}).get(key, 0) for key in keys}


def cmd_peer(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    messages = st.read_messages(run)
    problem = peers.launch_problem(state["peer"])
    if problem:
        raise st.DuetError(problem)
    blocker = _peer_blocker(state, messages, args.phase, args.write)
    if blocker:
        warn(blocker[1])
        return blocker[0]
    workspace = Path(state["workspace"])
    before = gates.fingerprint(workspace)
    authorship = _observed(state, before, state["host"])
    session = None if args.new_session else state["peer_session"]
    if args.new_session:
        # Rebuild the peer's memory from the record: everything said so far, its own words included.
        hidden = set(_delta(state, messages, args.phase)[1])
        delta, held = [m for m in messages if m["seq"] not in hidden and m["kind"] != "goal"], sorted(hidden)
    else:
        delta, held = _delta(state, messages, args.phase)
    changes = gates.change_summary(workspace) if args.phase == "review" else ""
    prompt = prompts.build_prompt(state, _goal(messages), args.phase, delta, args.write, session is None,
                                  retry=bool(state.get("last_peer_failed")) and session is not None,
                                  changes=changes)
    attempt = len(list((run / "turns").iterdir())) + 1
    result = peers.run_turn(
        state["peer"], prompt, workspace, run / "turns" / f"{attempt:03d}",
        session, args.write, state["peer_model"], args.timeout, state.get("peer_effort"),
        bool(state.get("peer_user_config")),
    )
    after = gates.fingerprint(workspace)
    if after != before:
        authorship = {"seen_fingerprint": after, "last_writer": state["peer"]}
    if not result.ok:
        st.append_message(run, "system", args.phase, f"Peer turn failed: {result.error}", kind="peer_error")
        fresh = st.load_state(run)
        st.save_state(run, st.updated(fresh, peer_session=result.session or session, last_peer_failed=True,
                                      peer_turns=fresh["peer_turns"] + 1, **authorship))
        warn(result.error)
        return EXIT_PEER_FAILED
    broke_read_only = not args.write and before != after
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
        peer_usage=_add_usage(fresh.get("peer_usage"), result.usage), last_peer_failed=False, **authorship,
    ))
    emit(result.reply)
    if broke_read_only:
        text = (f"Files in the workspace changed during a read-only turn by {state['peer']}. "
                "Inspect the change before continuing; any approval in that turn is not counted.")
        st.append_message(run, "system", args.phase, text, kind="readonly_violation")
        warn(text)
        return EXIT_READONLY_BROKEN
    if ignored:
        warn("the peer approved in a turn where it could edit files; that approval is not counted")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if not state.get("accept_cmd"):
        raise st.DuetError("no acceptance command is set; use `accept --cmd`")
    authorship = _observed(state, gates.fingerprint(Path(state["workspace"])), state["host"])
    result = gates.run_check(state, args.timeout)
    outcome = "PASSED" if result["passed"] else f"FAILED (exit {result['exit_code']})"
    text = f"Acceptance `{state['accept_cmd']}` {outcome}.\n\n```\n{result['output']}\n```"
    message = st.append_message(run, "system", "test", text, kind="check",
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
    text = f"Experiment `{args.cmd}` exited {code}.\n\n```\n{output}\n```"
    st.append_message(run, "system", "debate", text, kind="experiment", meta={"exit_code": code})
    emit(text)
    return EXIT_OK if code == 0 else EXIT_CHECK_FAILED


def cmd_accept(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if not args.cmd.strip():
        raise st.DuetError("the acceptance command is empty")
    started = any(m["phase"] != "frame" for m in st.read_messages(run) if m["speaker"] != "system")
    if started and not args.by_user:
        raise st.DuetError("the acceptance command is locked once the frame phase is over. Changing it now "
                           "needs the user's agreement: pass --by-user \"<their words>\"")
    st.save_state(run, st.updated(state, accept_cmd=args.cmd, accept_waived=None, last_check=None,
                                  fail_streak={"digest": None, "count": 0}))
    note = f" Changed with the user's agreement: {args.by_user}" if args.by_user else ""
    st.append_message(run, "system", "frame", f"Acceptance command set to `{args.cmd}`.{note}", kind="accept")
    emit("acceptance command recorded; earlier check results no longer count")
    return EXIT_OK


def cmd_extend(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if args.turns < 1:
        raise st.DuetError("--turns must be at least 1")
    new_cap = state["max_turns"] + args.turns
    st.save_state(run, st.updated(state, max_turns=new_cap))
    st.append_message(run, "system", "frame", f"Turn cap raised to {new_cap}: {args.reason}", kind="extend")
    emit(f"turn cap is now {new_cap}")
    return EXIT_OK


def _report(run: Path) -> Dict[str, Any]:
    state = st.load_state(run)
    messages = st.read_messages(run)
    ready, blocking = gates.finish_report(state, messages)
    return {
        "run": str(run), "status": state["status"], "host": state["host"], "peer": state["peer"],
        "peer_session": state["peer_session"], "peer_turns": state["peer_turns"],
        "max_turns": state["max_turns"], "messages": len(messages),
        "peer_usage": state.get("peer_usage"), "max_cost_usd": state.get("max_cost_usd"),
        "accept_cmd": state["accept_cmd"], "accept_waived": state["accept_waived"],
        "last_check_passed": (state["last_check"] or {}).get("passed"),
        "stalled": gates.is_stalled(state, messages),
        "last_writer": gates.current_writer(state, gates.fingerprint(Path(state["workspace"]))),
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
    st.append_message(run, "system", "review",
                      "Run finished: acceptance holds on the current files and both models approved them.",
                      kind="finish")
    st.save_state(run, st.updated(st.load_state(run), status="done"))
    emit(f"Finished. Transcript: {report['transcript']}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="duet", description="Two models, one conversation, one build.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="start a run inside a workspace")
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
    say.add_argument("--phase", required=True, choices=st.PHASES)
    say.add_argument("--speaker")
    say.add_argument("--file", help="file holding the message; stdin if omitted")
    say.add_argument("--verdict", choices=("approve", "revise"))
    say.set_defaults(func=cmd_say)

    peer = sub.add_parser("peer", help="give the peer everything new and record its reply")
    peer.add_argument("--run", required=True)
    peer.add_argument("--phase", required=True, choices=st.PHASES)
    peer.add_argument("--write", action="store_true", help="let the peer edit the workspace this turn")
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
        return args.func(args)
    except (st.DuetError, OSError) as exc:
        warn(str(exc))
        return EXIT_USAGE
