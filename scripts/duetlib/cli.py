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
    run = st.new_run(
        Path(args.workspace), _read_text(args.goal_file), args.peer,
        args.accept, args.waive_accept, args.max_turns, args.peer_model, args.peer_effort,
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
    meta: Dict[str, Any] = {}
    if args.verdict:
        meta = {"verdict": args.verdict, "fingerprint": gates.fingerprint(Path(state["workspace"]))}
    message = st.append_message(run, speaker, args.phase, text, meta=meta)
    if speaker == "user":
        st.save_state(run, st.updated(state, fail_streak={"digest": None, "count": 0}))
    emit(f"recorded message {message['seq']} from {speaker}")
    return EXIT_OK


def _delta(state: st.State, messages: List[st.Message], phase: str) -> Tuple[List[st.Message], List[int]]:
    """Messages the peer has not seen. In the blind phase the host's proposal is held back."""
    pending = [
        m for m in messages
        if m["speaker"] != state["peer"] and (m["seq"] > state["peer_cursor"] or m["seq"] in state["withheld"])
    ]
    if phase != "propose":
        return pending, []
    held = [m["seq"] for m in pending if m["speaker"] == state["host"] and m["phase"] == "propose"]
    return [m for m in pending if m["seq"] not in held], held


def _peer_blocker(state: st.State, messages: List[st.Message], phase: str) -> Optional[Tuple[int, str]]:
    if state["status"] != "active":
        return EXIT_USAGE, "this run is finished; start a new one"
    if state["peer_turns"] >= state["max_turns"]:
        return EXIT_CAP, (f"turn cap reached ({state['max_turns']}). Stop and report to the user; "
                          "continue only if they say so, with `extend`")
    if gates.is_stalled(state, messages):
        return EXIT_STALLED, (f"the acceptance command failed {gates.STALL_AFTER} times the same way. "
                              "Stop and ask the user; record their answer with `say --speaker user`")
    host_proposed = any(m["speaker"] == state["host"] and m["phase"] == "propose" for m in messages)
    if phase == "propose" and not host_proposed:
        return EXIT_BLIND, (f"blind round: record {state['host']}'s own proposal first "
                            "(`say --phase propose`), then ask the peer")
    return None


def cmd_peer(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    messages = st.read_messages(run)
    blocker = _peer_blocker(state, messages, args.phase)
    if blocker:
        warn(blocker[1])
        return blocker[0]
    delta, held = _delta(state, messages, args.phase)
    first = state["peer_session"] is None
    prompt = prompts.build_prompt(state, _goal(messages), args.phase, delta, args.write, first)
    attempt = len(list((run / "turns").iterdir())) + 1
    result = peers.run_turn(
        state["peer"], prompt, Path(state["workspace"]), run / "turns" / f"{attempt:03d}",
        state["peer_session"], args.write, state["peer_model"], args.timeout, state.get("peer_effort"),
    )
    if not result.ok:
        st.append_message(run, "system", args.phase, f"Peer turn failed: {result.error}", kind="peer_error")
        fresh = st.load_state(run)
        st.save_state(run, st.updated(fresh, peer_session=result.session, peer_turns=fresh["peer_turns"] + 1))
        warn(result.error)
        return EXIT_PEER_FAILED
    verdict = gates.parse_verdict(result.reply)
    ignored = verdict if (args.write and verdict == "approve") else None
    meta = {
        "verdict": None if ignored else verdict,
        "verdict_ignored": ignored,
        "write": args.write,
        "fingerprint": gates.fingerprint(Path(state["workspace"])),
    }
    st.append_message(run, state["peer"], args.phase, result.reply, meta=meta)
    fresh = st.load_state(run)  # the turn can take a long time; do not overwrite changes made meanwhile
    st.save_state(run, st.updated(
        fresh, peer_session=result.session, peer_cursor=messages[-1]["seq"] if messages else 0,
        withheld=held, peer_turns=fresh["peer_turns"] + 1,
    ))
    emit(result.reply)
    if ignored:
        warn("the peer approved in a turn where it could edit files; that approval is not counted")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if not state.get("accept_cmd"):
        raise st.DuetError("no acceptance command is set; use `accept --cmd`")
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
    ))
    emit(text)
    return EXIT_OK if result["passed"] else EXIT_CHECK_FAILED


def cmd_accept(args: argparse.Namespace) -> int:
    run = Path(args.run)
    state = st.load_state(run)
    if not args.cmd.strip():
        raise st.DuetError("the acceptance command is empty")
    st.save_state(run, st.updated(state, accept_cmd=args.cmd, accept_waived=None, last_check=None,
                                  fail_streak={"digest": None, "count": 0}))
    st.append_message(run, "system", "frame", f"Acceptance command set to `{args.cmd}`.", kind="accept")
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
        "accept_cmd": state["accept_cmd"], "accept_waived": state["accept_waived"],
        "last_check_passed": (state["last_check"] or {}).get("passed"),
        "stalled": gates.is_stalled(state, messages),
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
    peer.add_argument("--timeout", type=int, default=peers.DEFAULT_TIMEOUT_SECONDS)
    peer.set_defaults(func=cmd_peer)

    check = sub.add_parser("check", help="run the acceptance command and record the result")
    check.add_argument("--run", required=True)
    check.add_argument("--timeout", type=int, default=gates.CHECK_TIMEOUT_SECONDS)
    check.set_defaults(func=cmd_check)

    accept = sub.add_parser("accept", help="set or replace the acceptance command")
    accept.add_argument("--run", required=True)
    accept.add_argument("--cmd", required=True)
    accept.set_defaults(func=cmd_accept)

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
