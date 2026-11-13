"""Command line: run arms on tasks, verify the tasks themselves, and print the comparison table."""
from __future__ import annotations

import argparse
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import arms, runner, scoring, summary
from .tasks import Task, TaskError, copy_tree, discover, prepare_workspace

EVAL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_DIR = EVAL_ROOT / "tasks"
DEFAULT_RESULTS = Path.home() / ".duet-eval" / "results"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_MAX_COST = 6.0
ALL = "all"
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2
Plan = List[Tuple[Task, str, int]]


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
        return list(arms.ARMS)
    unknown = [name for name in names if name not in arms.ARMS]
    if unknown:
        raise TaskError(f"unknown arm(s): {', '.join(unknown)}; available: {', '.join(arms.ARMS)}")
    return names


def make_plan(tasks: Sequence[Task], arm_names: Sequence[str], seeds: int) -> Plan:
    """Seeds outermost, so an interrupted matrix still leaves whole seeds to compare."""
    return [(task, arm, seed) for seed in range(1, seeds + 1) for task in tasks for arm in arm_names]


def _options(args: argparse.Namespace, session_dir: Path) -> runner.RunOptions:
    return runner.RunOptions(
        session_dir=session_dir, timeout=args.timeout, max_cost=args.max_cost, effort=args.effort,
        duet_turns=args.duet_turns, keep_workspaces=args.keep_workspaces,
        skill_root=Path(args.skill_root).resolve())


def _dry_run(plan: Plan, options: runner.RunOptions, show_prompt: bool) -> None:
    for task, arm, seed in plan:
        workspace = options.session_dir / "work" / "<fresh-id>" / "ws"
        logs = options.session_dir / "logs" / runner.run_id(task.name, arm, seed)
        launch = runner.build_launch(task, arm, workspace, logs / "last_message.txt", options)
        emit(f"# {runner.run_id(task.name, arm, seed)}")
        emit(f"  cwd:     {workspace}")
        emit(f"  command: {shlex.join(launch.argv)}")
        emit(f"  stdin:   prompt, {len(launch.prompt)} characters (never placed on a command line)")
        emit(f"  limits:  {options.timeout}s wall clock, max cost {options.max_cost}, effort {options.effort}")
        if show_prompt:
            emit("  prompt:\n" + "\n".join("    | " + line for line in launch.prompt.splitlines()))
    emit(f"\nDry run: {len(plan)} run(s) planned, nothing was started.")


def _line(record: Dict[str, Any]) -> str:
    cost = "-" if record["cost_usd"] is None else f"${record['cost_usd']:.2f}"
    duet = "" if not record["is_duet"] else f" duet_finished={record['duet']['finished']}"
    return (f"  {record['status']:<11} visible={'pass' if record['visible']['passed'] else 'FAIL'} "
            f"hidden={'pass' if record['hidden']['passed'] else 'FAIL'} "
            f"{record['seconds']:.0f}s {cost}{duet}")


def cmd_run(args: argparse.Namespace) -> int:
    if args.seeds < 1 or args.timeout < 1 or args.duet_turns < 1:
        raise TaskError("--seeds, --timeout and --duet-turns must be at least 1")
    if args.max_cost is not None and args.max_cost <= 0:
        raise TaskError("--max-cost must be greater than 0")
    tasks = discover(Path(args.tasks_dir), _names(args.tasks))
    plan = make_plan(tasks, _arm_names(args.arms), args.seeds)
    results = Path(args.results).expanduser().resolve()
    session_dir = results / (args.session or time.strftime("%Y%m%d-%H%M%S"))
    options = _options(args, session_dir)
    if args.dry_run:
        _dry_run(plan, options, args.show_prompt)
        return EXIT_OK
    repository = runner.in_git_repository(results)
    if repository is not None:
        raise TaskError(f"--results {results} is inside the repository at {repository}; "
                        "runs must happen outside your projects")
    emit(f"{len(plan)} run(s); records in {session_dir / 'records'}")
    spent = 0.0
    for index, (task, arm, seed) in enumerate(plan, start=1):
        if args.max_total_cost is not None and spent >= args.max_total_cost:
            emit(f"Stopping before run {index}: reported spend ${spent:.2f} reached --max-total-cost.")
            return EXIT_FAILED
        emit(f"[{index}/{len(plan)}] {runner.run_id(task.name, arm, seed)}")
        record = runner.run_one(task, arm, seed, options)
        spent += record["cost_usd"] or 0.0
        emit(_line(record))
    emit()
    emit(summary.format_summary(summary.load_records(session_dir)))
    return EXIT_OK


def cmd_summary(args: argparse.Namespace) -> int:
    root = Path(args.results).expanduser()
    records = summary.load_records(root)
    emit(summary.format_summary(records))
    if args.by_task and records:
        emit()
        emit(summary.format_by_task(records))
    return EXIT_OK if records else EXIT_FAILED


def cmd_list(args: argparse.Namespace) -> int:
    for task in discover(Path(args.tasks_dir)):
        emit(f"{task.name}\t{task.accept}")
    emit("arms: " + ", ".join(arms.ARMS))
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_eval", description="Compare a duet against one model working alone.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run arms on tasks and record the results")
    run.add_argument("--tasks", required=True, help="comma-separated task names, or 'all'")
    run.add_argument("--arms", required=True, help=f"comma-separated arms, or 'all' ({', '.join(arms.ARMS)})")
    run.add_argument("--seeds", type=int, default=1, help="repetitions of each (task, arm); default 1")
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="wall-clock seconds per run")
    run.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST,
                     help="dollar cap per run where the client can enforce one (Claude host or Claude peer)")
    run.add_argument("--max-total-cost", type=float,
                     help="stop starting new runs once reported spend reaches this many dollars")
    run.add_argument("--effort", help="reasoning effort passed to both clients (e.g. low, medium, high)")
    run.add_argument("--duet-turns", type=int, default=arms.DEFAULT_DUET_TURNS, help="peer turn cap for duet arms")
    run.add_argument("--dry-run", action="store_true", help="print the commands without running anything")
    run.add_argument("--show-prompt", action="store_true", help="with --dry-run, also print each prompt")
    run.add_argument("--results", default=str(DEFAULT_RESULTS), help="results root (outside any repository)")
    run.add_argument("--session", help="name of this session's folder under the results root; default a timestamp")
    run.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    run.add_argument("--skill-root", default=str(runner.SKILL_ROOT), help="duet checkout the hosts are pointed at")
    run.add_argument("--keep-workspaces", action="store_true", help="keep each finished workspace for inspection")
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("summary", help="print the comparison table from recorded runs")
    report.add_argument("--results", default=str(DEFAULT_RESULTS), help="results root or one session folder")
    report.add_argument("--by-task", action="store_true", help="also print a task-by-arm grid")
    report.set_defaults(func=cmd_summary)

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
