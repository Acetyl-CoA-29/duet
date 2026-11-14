"""Command line: run arms on tasks, verify the tasks themselves, and print the comparison tables."""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import arms, layout, plan, report, runner, scoring, summary
from .tasks import Task, TaskError, copy_tree, discover, prepare_workspace

EVAL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_DIR = EVAL_ROOT / "tasks"
DEFAULT_RESULTS = Path.home() / ".duet-eval" / "results"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_MAX_COST = 6.0
ALL = "all"
STAGED_TASKS = "tasks"
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2
Plan = List[plan.PlannedRun]


def emit(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _names(value: str) -> Optional[List[str]]:
    """A comma-separated list, or None for "all"."""
    if value.strip().lower() == ALL:
        return None
    names = [part.strip() for part in value.split(",") if part.strip()]
    if not names:
        raise TaskError("give at least one name, or 'all'")
    return names


def _arm_names(value: str) -> List[str]:
    names = _names(value)
    if names is None:
        return arms.arm_names()
    unknown = [name for name in names if name not in arms.arm_names()]
    if unknown:
        raise TaskError(f"unknown arm(s): {', '.join(unknown)}; available: {', '.join(arms.arm_names())}")
    return names


def _options(args: argparse.Namespace, results: Path, session_dir: Path) -> runner.RunOptions:
    """Duet hosts are pointed at a copy of the skill beside the work folder, never at the checkout."""
    work = Path(args.work_dir).expanduser().resolve() if args.work_dir else layout.default_work_root()
    return runner.RunOptions(
        session_dir=session_dir, timeout=args.timeout, max_cost=args.max_cost, effort=args.effort,
        duet_turns=args.duet_turns, keep_workspaces=args.keep_workspaces,
        skill_root=work.parent / f"skill-{session_dir.name}", skill_source=Path(args.skill_root).resolve(),
        work_root=work, duet_timeout=args.duet_timeout,
        watch=(results, Path(args.tasks_dir).expanduser().resolve()))


def _members(arm: str) -> Sequence[str]:
    return arms.COMPOSITE_ARMS.get(arm, (arm,))


def _budget_notes(arm_names: Sequence[str], options: runner.RunOptions) -> List[str]:
    """One line for each kind of arm whose limits differ from a solo run's, and for any effort gap."""
    notes = {arms.timeout_note(arm, options.timeout, options.duet_turns, options.duet_timeout): None for arm in arm_names}
    if options.effort and "duet-codex-host" in arm_names:
        notes[f"duet-codex-host: {arms.CLAUDE_PEER_EFFORT_NOTE}"] = None
    return [note for note in notes if note]


def _dry_run(runs: Plan, options: runner.RunOptions, show_prompt: bool) -> None:
    for run in runs:
        emit(f"# {runner.run_id(run.task.name, run.arm, run.seed)}  (position {run.position} of {run.of})")
        budget = runner.budget_for(run.arm, options)
        for member in _members(run.arm):
            workspace = runner.work_root(options) / "<fresh-id>" / layout.WORKSPACE_NAME
            logs = options.session_dir / "logs" / runner.run_id(run.task.name, run.arm, run.seed)
            launch = runner.build_launch(run.task, member, workspace, logs / "last_message.txt", options, budget)
            emit(f"  cwd:     {workspace}")
            emit(f"  command: {shlex.join(launch.argv)}")
            emit(f"  stdin:   prompt, {len(launch.prompt)} characters (never placed on a command line)")
            emit(f"  limits:  {budget[0]}s wall clock, max cost {budget[1]}, effort {options.effort}")
            if show_prompt:
                emit("  prompt:\n" + "\n".join("    | " + line for line in launch.prompt.splitlines()))
    emit(f"\nDry run: {len(runs)} run(s) planned, nothing was started.")


def _line(record: Dict[str, Any]) -> str:
    cost = "-" if record["cost_usd"] is None else f"${record['cost_usd']:.2f}"
    duet = "" if not record["is_duet"] else f" duet_finished={record['duet']['finished']}"
    picked = f" picked={record['picked']}" if record.get("picked") else ""
    infra = f"\n  infrastructure failure: {record['infra_failure']}" if record["infra_failure"] else ""
    return (f"  {record['status']:<11} visible={'pass' if record['visible']['passed'] else 'FAIL'} "
            f"hidden={'pass' if record['hidden']['passed'] else 'FAIL'} "
            f"{record['seconds']:.0f}s {cost}{duet}{picked}{infra}")


def _write_manifest(session_dir: Path, invocation: Dict[str, Any], earlier: List[Dict[str, Any]]) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / summary.MANIFEST).write_text(
        json.dumps({"invocations": [*earlier, invocation]}, indent=2) + "\n", encoding="utf-8")


def _earlier_invocations(session_dir: Path) -> List[Dict[str, Any]]:
    try:
        found = json.loads((session_dir / summary.MANIFEST).read_text(encoding="utf-8")).get("invocations")
    except (OSError, ValueError, AttributeError):
        return []
    return [item for item in found or [] if isinstance(item, dict)]


def _execute(runs: Plan, options: runner.RunOptions, order_seed: int, max_total: Optional[float]) -> int:
    """Run the plan in order, keeping the session's manifest current. Returns how the session ended."""
    earlier = _earlier_invocations(options.session_dir)
    invocation: Dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "order_seed": order_seed,
        "limits": runner.session_limits(options),
        "planned": [runner.run_id(run.task.name, run.arm, run.seed) for run in runs],
        "completed": 0, "stopped_early": None}
    _write_manifest(options.session_dir, invocation, earlier)
    spent = 0.0
    for index, run in enumerate(runs, start=1):
        if max_total is not None and spent >= max_total:
            reason = f"reported spend ${spent:.2f} reached --max-total-cost before run {index} of {len(runs)}"
            emit(f"Stopping before run {index}: reported spend ${spent:.2f} reached --max-total-cost.")
            _write_manifest(options.session_dir, {**invocation, "completed": index - 1, "stopped_early": reason}, earlier)
            return EXIT_FAILED
        emit(f"[{index}/{len(runs)}] {runner.run_id(run.task.name, run.arm, run.seed)}")
        record = runner.run_one(run.task, run.arm, run.seed, options,
                                {"seed": order_seed, "position": run.position, "of": run.of})
        spent += record["cost_usd"] or 0.0
        emit(_line(record))
        _write_manifest(options.session_dir, {**invocation, "completed": index}, earlier)
    return EXIT_OK


def _check_run_args(args: argparse.Namespace) -> None:
    if args.seeds < 1 or args.timeout < 1 or args.duet_turns < 1:
        raise TaskError("--seeds, --timeout and --duet-turns must be at least 1")
    if args.duet_timeout is not None and args.duet_timeout < 1:
        raise TaskError("--duet-timeout must be at least 1")
    if args.max_cost is not None and args.max_cost <= 0:
        raise TaskError("--max-cost must be greater than 0")


def cmd_run(args: argparse.Namespace) -> int:
    _check_run_args(args)
    tasks = discover(Path(args.tasks_dir), _names(args.tasks))
    arm_names = _arm_names(args.arms)
    order_seed = args.order_seed if args.order_seed is not None else plan.new_order_seed()
    results = Path(args.results).expanduser().resolve()
    session_dir = results / (args.session or time.strftime("%Y%m%d-%H%M%S"))
    options = _options(args, results, session_dir)
    notes = [f"arm order shuffled per task and seed from order seed {order_seed}", *_budget_notes(arm_names, options)]
    if args.dry_run:
        emit("\n".join(notes))
        _dry_run(plan.make_plan(tasks, arm_names, args.seeds, order_seed), options, args.show_prompt)
        return EXIT_OK
    repository = layout.in_git_repository(results)
    if repository is not None:
        raise TaskError(f"--results {results} is inside the repository at {repository}; "
                        "runs must happen outside your projects")
    layout.check_apart(runner.work_root(options), runner.kept_apart(options))
    if any(arms.is_duet(arm) for arm in arm_names) and options.skill_source is not None:
        layout.check_skill(options.skill_source)
    try:
        runs = plan.make_plan(layout.stage_tasks(tasks, session_dir / STAGED_TASKS), arm_names, args.seeds, order_seed)
        emit(f"{len(runs)} run(s); records in {session_dir / 'records'}")
        emit("\n".join(notes))
        code = _execute(runs, options, order_seed, args.max_total_cost)
    finally:
        shutil.rmtree(str(options.skill_root), ignore_errors=True)
    emit()
    emit(_session_report(summary.load_sessions(session_dir)[session_dir.name]))
    return code


def _session_report(session: summary.Session, by_task: bool = False, legend: bool = True) -> str:
    notes = [f"UNBALANCED: this session stopped early ({reason}); some arms ran fewer cells than others."
             for reason in session.stopped_early]
    if session.superseded:
        notes.append(f"{session.superseded} superseded record(s) from earlier attempts are kept beside the "
                     "records and are not counted.")
    text = report.format_summary(session.records, f"Session {session.name}", notes, legend)
    return text + ("\n\n" + report.format_by_task(session.records) if by_task and session.records else "")


def _merged_report(sessions: Dict[str, summary.Session], allow_mixed: bool, by_task: bool) -> str:
    records = [record for session in sessions.values() for record in session.records]
    limits = summary.distinct_limits(records)
    if len(limits) > 1 and not allow_mixed:
        raise TaskError(f"these {len(sessions)} sessions ran under different limits "
                        f"({', '.join(summary.LIMIT_FIELDS)}): {'; '.join(map(str, limits))}. Their results are "
                        "not comparable; pass --allow-mixed-limits to merge them anyway")
    notes = ["MIXED LIMITS: these sessions ran under different limits; treat the rates as a rough total only."] \
        if len(limits) > 1 else []
    repeated = summary.repeated_cells(records)
    if repeated:
        notes.append(f"{repeated} (task, arm, seed) combination(s) appear in more than one session; "
                     "every record is counted as its own run.")
    text = report.format_summary(records, f"{len(sessions)} sessions merged: {', '.join(sessions)}", notes)
    return text + ("\n\n" + report.format_by_task(records) if by_task and records else "")


def cmd_summary(args: argparse.Namespace) -> int:
    """One table per session unless --merge is given: sessions differ in limits, order and date."""
    sessions = {name: session for name, session in summary.load_sessions(Path(args.results).expanduser()).items()
                if session.records}
    if not sessions:
        emit("No records found.")
        return EXIT_FAILED
    if args.merge and len(sessions) > 1:
        emit(_merged_report(sessions, args.allow_mixed_limits, args.by_task))
    else:
        last = list(sessions)[-1]                      # the column notes are printed once, after the last table
        emit("\n\n".join(_session_report(session, args.by_task, name == last) for name, session in sessions.items()))
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    for task in discover(Path(args.tasks_dir)):
        emit(f"{task.name}\t{task.accept}")
    emit("arms: " + ", ".join(arms.arm_names()))
    return EXIT_OK


def _verify_variant(task: Task, kind: Optional[str], scratch: Path) -> Optional[scoring.Score]:
    """Score the starter with one solution overlay (or none); None when the task ships no such overlay."""
    if kind is not None and not task.solution(kind).is_dir():
        return None
    workspace = scratch / (kind or "starter") / "ws"
    prepare_workspace(task, workspace)
    if kind is not None:
        copy_tree(task.solution(kind), workspace)
    return scoring.score_workspace(task, workspace, workspace.parent)


def cmd_verify(args: argparse.Namespace) -> int:
    """Each task must be worth running: the starter fails, the reference passes everything, and
    the naive solution passes the visible command while failing the oracle."""
    expected = {None: (False, False), "reference": (True, True), "naive": (True, False)}
    problems = 0
    with tempfile.TemporaryDirectory(prefix="duet-eval-verify-") as scratch:
        for task in discover(Path(args.tasks_dir), _names(args.tasks)):
            for kind, want in expected.items():
                label = f"{task.name}/{kind or 'starter'}"
                score = _verify_variant(task, kind, Path(scratch) / task.name)
                if score is None:
                    emit(f"MISSING {label}: no solutions/{kind} folder")
                    problems += 1
                    continue
                got = (score.visible.passed, score.hidden.passed)
                good = got == want if kind else not score.hidden.passed
                problems += 0 if good else 1
                emit(f"{'ok     ' if good else 'WRONG  '} {label}: visible={got[0]} hidden={got[1]} "
                     f"(hidden tests {score.hidden.tests_passed} passed, {score.hidden.tests_failed} failed)")
    emit("All tasks behave as designed." if not problems else f"{problems} problem(s).")
    return EXIT_OK if not problems else EXIT_FAILED


def _add_run_parser(sub: Any) -> None:
    run = sub.add_parser("run", help="run arms on tasks and record the results")
    run.add_argument("--tasks", required=True, help="comma-separated task names, or 'all'")
    run.add_argument("--arms", required=True, help=f"comma-separated arms, or 'all' ({', '.join(arms.arm_names())})")
    run.add_argument("--seeds", type=int, default=1, help="repetitions of each (task, arm); default 1")
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                     help="wall-clock seconds for a solo run; duet and best-of-two limits are derived from it")
    run.add_argument("--duet-timeout", type=int, help="wall-clock seconds for a duet run, instead of the derived limit")
    run.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST,
                     help="dollar cap per run where the client can enforce one (Claude host or Claude peer)")
    run.add_argument("--max-total-cost", type=float,
                     help="stop starting new runs once reported spend reaches this many dollars")
    run.add_argument("--effort", help="reasoning effort (e.g. low, medium, high) for every client that takes one")
    run.add_argument("--duet-turns", type=int, default=arms.DEFAULT_DUET_TURNS, help="peer turn cap for duet arms")
    run.add_argument("--order-seed", type=int, help="seed for the per-task arm order; default a fresh one, recorded")
    run.add_argument("--dry-run", action="store_true", help="print the commands without running anything")
    run.add_argument("--show-prompt", action="store_true", help="with --dry-run, also print each prompt")
    run.add_argument("--results", default=str(DEFAULT_RESULTS), help="results root (outside any repository)")
    run.add_argument("--work-dir", help="where throwaway workspaces are made; default a folder under the temp "
                                        "folder, away from the results")
    run.add_argument("--session", help="name of this session's folder under the results root; default a timestamp")
    run.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR), help="where tasks are copied from before a run")
    run.add_argument("--skill-root", default=str(runner.SKILL_ROOT),
                     help="duet checkout; hosts are pointed at a copy of it without eval/ and tests/")
    run.add_argument("--keep-workspaces", action="store_true",
                     help="keep each finished workspace (moved beside the results) for inspection")
    run.set_defaults(func=cmd_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_eval", description="Compare a duet against one model working alone.")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)

    summarise = sub.add_parser("summary", help="print the comparison tables from recorded runs")
    summarise.add_argument("--results", default=str(DEFAULT_RESULTS), help="results root or one session folder")
    summarise.add_argument("--by-task", action="store_true", help="also print a task-by-arm grid")
    summarise.add_argument("--merge", action="store_true",
                           help="one table over every session instead of one table per session")
    summarise.add_argument("--allow-mixed-limits", action="store_true",
                           help="with --merge, merge sessions even though they ran under different limits")
    summarise.set_defaults(func=cmd_summary)

    listing = sub.add_parser("list", help="list tasks and arms")
    listing.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    listing.set_defaults(func=cmd_list)

    verify = sub.add_parser("verify", help="check each task against its reference and naive solutions (no model calls)")
    verify.add_argument("--tasks", default=ALL)
    verify.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (TaskError, ValueError) as exc:
        sys.stderr.write(f"run_eval: {exc}\n")
        return EXIT_USAGE
