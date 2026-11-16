"""Command line for the review micro-benchmark: list and verify the cases, run the reviews under
hard caps (or only print the plan), and summarise a session."""
from __future__ import annotations

import argparse
import shlex
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from evallib import layout
from evallib.tasks import TaskError

from . import bench, clients, plan, report, store, verify
from .cases import DEFAULT_CASES_DIR, Case, CaseError, discover
from .prompts import review_prompt

DEFAULT_RESULTS = Path.home() / ".duet-eval" / "reviewbench"
DEFAULT_MAX_TOTAL_COST = 20.0
ALL = "all"
VERIFY_WORKERS = 6
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2
# A session's cells are only comparable when these match, so a resumed session must repeat them.
FIXED_SETTINGS = ("cases", "samples", "effort", "claude_model", "codex_model")


def emit(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def default_work_root() -> Path:
    return layout.stage_root() / "reviewbench-work"


def _names(value: str) -> Optional[List[str]]:
    """A comma-separated list, or None for "all"."""
    if value.strip().lower() == ALL:
        return None
    names = [part.strip() for part in value.split(",") if part.strip()]
    if not names:
        raise CaseError("give at least one case name, or 'all'")
    return names


def cmd_list(args: argparse.Namespace) -> int:
    cases = discover(Path(args.cases_dir))
    for case in cases:
        kind = "control" if case.key is None else case.key.defect_class
        emit(f"{case.name}\t{case.line_count} lines\t{kind}")
    seeded = sum(1 for case in cases if not case.control)
    emit(f"{seeded} seeded case(s), {len(cases) - seeded} clean control(s)")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """Each seeded case's defect is real, survives its happy-path tests, and is where the key says;
    each control passes its tests. No model is called."""
    problems = 0
    with tempfile.TemporaryDirectory(prefix="duet-reviewbench-verify-") as scratch:
        cases = discover(Path(args.cases_dir), _names(args.cases))
        with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as pool:     # each check is a pytest subprocess
            results = list(pool.map(lambda case: verify.verify_case(case, Path(scratch)), cases))
        for case, found in zip(cases, results):
            problems += len(found)
            emit(f"{'ok     ' if not found else 'WRONG  '} {case.name} ({'control' if case.control else 'seeded'})")
            for problem in found:
                emit(f"         {problem}")
    emit("All cases behave as designed." if not problems else f"{problems} problem(s).")
    return EXIT_OK if not problems else EXIT_FAILED


def _check_run_args(args: argparse.Namespace) -> None:
    if args.samples < 1 or args.timeout < 1:
        raise CaseError("--samples and --timeout must be at least 1")
    if args.max_calls is not None and args.max_calls < 1:
        raise CaseError("--max-calls must be at least 1")
    for name in ("max_call_cost", "max_total_cost"):
        if getattr(args, name) <= 0:
            raise CaseError(f"--{name.replace('_', '-')} must be greater than 0")


def _settings(args: argparse.Namespace, cases: Sequence[Case]) -> Dict[str, Any]:
    return {"cases": [case.name for case in cases], "samples": args.samples, "effort": args.effort,
            "claude_model": args.claude_model, "codex_model": args.codex_model,
            "timeout": args.timeout, "max_call_cost": args.max_call_cost}


def _check_same_settings(earlier: Sequence[Dict[str, Any]], settings: Dict[str, Any]) -> None:
    """Refuse to continue a session with different cases, models, effort or sample count: its cells
    would no longer be comparable with one another."""
    for invocation in earlier:
        before = invocation.get("settings", {})
        changed = [name for name in FIXED_SETTINGS if name in before and before[name] != settings[name]]
        if changed:
            raise CaseError(f"this session was started with different {', '.join(changed)}; use the same "
                            "values to resume it, or a new --session")


def _order_seed(args: argparse.Namespace, earlier: Sequence[Dict[str, Any]]) -> int:
    """A resumed session keeps its order, so the cells left undone are the same ones."""
    if args.order_seed is not None:
        return int(args.order_seed)
    seeds = [item["order_seed"] for item in earlier if isinstance(item.get("order_seed"), int)]
    return seeds[-1] if seeds else plan.new_order_seed()


def _calls_left(cells: Sequence[plan.Cell], records: Dict[str, store.Record], retry_errors: bool) -> Dict[str, int]:
    """Calls a run would start now, per reviewer, given what the session already holds."""
    left = {reviewer: 0 for reviewer in clients.REVIEWERS}
    for cell in cells:
        earlier = records.get(cell.id)
        if earlier is not None and earlier.get("status") == store.ERROR and not retry_errors:
            continue
        left = {**left, cell.reviewer: left[cell.reviewer] + len(bench.remaining_steps(cell, earlier))}
    return left


def _plan_lines(cases: Sequence[Case], cells: Sequence[plan.Cell], left: Dict[str, int],
                options: bench.RunOptions, order_seed: int) -> List[str]:
    seeded = sum(1 for case in cases if not case.control)
    done = store.calls_made(store.load_ledger(options.session_dir))
    worst = left[clients.CLAUDE] * (options.client.max_call_cost or 0.0)
    return [
        f"{len(cases)} case(s) ({seeded} seeded, {len(cases) - seeded} control) x {len(clients.REVIEWERS)} reviewers "
        f"x (self: 2 calls, fresh: 1 call) = {len(cells)} cell(s), {plan.planned_calls(cells)} call(s) in the full plan",
        f"order shuffled from order seed {order_seed}",
        f"calls already made in this session: {done}",
        "calls this run would make: " + ", ".join(f"{name} {count}" for name, count in left.items())
        + f" = {sum(left.values())}",
        f"caps: at most {options.max_calls} call(s) in the session; Claude's reported dollars at most "
        f"${options.max_total_cost:.2f} in the session, ${options.client.max_call_cost:.2f} per call "
        f"(so these Claude calls cost at most ${min(worst, options.max_total_cost or worst):.2f}); "
        "Codex reports no dollars, so only the call cap and the per-call timeout bound it",
    ]


def _dry_run(cases: Sequence[Case], lines: Sequence[str], options: bench.RunOptions, calls: int,
             show_prompt: bool) -> None:
    emit("\n".join(lines))
    for reviewer in clients.REVIEWERS:
        argv = clients.build_argv(reviewer, options.client, options.session_dir / store.LOGS / "<cell>" / "last.txt")
        emit(f"{reviewer} command (prompt on stdin, cwd an empty folder under {options.work_root}):")
        emit(f"  {shlex.join(argv)}")
    if show_prompt and cases:
        emit(f"review prompt for {cases[0].name}:\n" + "\n".join("    | " + line for line in review_prompt(cases[0]).splitlines()))
    emit(f"\nDry run: {calls} call(s) would be made, nothing was started.")


def _run_options(args: argparse.Namespace, results: Path, session_dir: Path, cases_dir: Path,
                 planned: int) -> bench.RunOptions:
    client = clients.ClientOptions(timeout=args.timeout, max_call_cost=args.max_call_cost, effort=args.effort,
                                   claude_model=args.claude_model, codex_model=args.codex_model,
                                   deny_read=(cases_dir, results))
    work = Path(args.work_dir).expanduser().resolve() if args.work_dir else default_work_root()
    return bench.RunOptions(session_dir=session_dir, work_root=work, client=client,
                            max_calls=args.max_calls if args.max_calls is not None else planned,
                            max_total_cost=args.max_total_cost, retry_errors=args.retry_errors)


def _check_layout(results: Path, options: bench.RunOptions, cases_dir: Path) -> None:
    repository = layout.in_git_repository(results)
    if repository is not None:
        raise CaseError(f"--results {results} is inside the repository at {repository}; "
                        "results must be kept outside your projects")
    layout.check_apart(options.work_root, (cases_dir, results))


def cmd_run(args: argparse.Namespace) -> int:
    _check_run_args(args)
    cases_dir = Path(args.cases_dir).expanduser().resolve()
    cases = discover(cases_dir, _names(args.cases))
    results = Path(args.results).expanduser().resolve()
    session_dir = results / (args.session or time.strftime("%Y%m%d-%H%M%S"))
    earlier = store.load_invocations(session_dir)
    settings = _settings(args, cases)
    _check_same_settings(earlier, settings)
    order_seed = _order_seed(args, earlier)
    cells = plan.make_plan(cases, args.samples, order_seed)
    options = _run_options(args, results, session_dir, cases_dir, plan.planned_calls(cells))
    left = _calls_left(cells, store.load_records(session_dir), args.retry_errors)
    lines = _plan_lines(cases, cells, left, options, order_seed)
    if args.dry_run:
        _dry_run(cases, lines, options, sum(left.values()), args.show_prompt)
        return EXIT_OK
    _check_layout(results, options, cases_dir)
    emit("\n".join(lines))
    emit(f"records in {session_dir}")
    invocation = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "order_seed": order_seed,
                  "settings": settings, "max_calls": options.max_calls, "max_total_cost": options.max_total_cost,
                  "cells": len(cells), "planned_calls": plan.planned_calls(cells), "finished": False}
    store.write_manifest(session_dir, earlier, invocation)
    outcome = bench.run_plan(cells, {case.name: case for case in cases}, options, emit)
    store.write_manifest(session_dir, earlier, {
        **invocation, "finished": True, "calls": outcome.calls, "stopped": outcome.stopped,
        "undone": list(outcome.undone), "failed": list(outcome.failed)})
    emit()
    emit(report.format_session(session_dir, cases))
    if outcome.stopped:
        emit(f"\n{len(outcome.undone)} cell(s) left undone. Resume with the same command and --session {session_dir.name}.")
    return EXIT_OK if not outcome.stopped and not outcome.failed else EXIT_FAILED


def cmd_summary(args: argparse.Namespace) -> int:
    """One report per session: sessions may differ in models, effort and cases, so none are merged."""
    root = Path(args.results).expanduser().resolve()
    sessions = [root] if store.is_session(root) else sorted(
        path for path in (root.iterdir() if root.is_dir() else []) if store.is_session(path))
    if not sessions:
        emit("No sessions found.")
        return EXIT_FAILED
    all_cases = discover(Path(args.cases_dir))
    reports = []
    for session_dir in sessions:
        named = [item.get("settings", {}).get("cases") for item in store.load_invocations(session_dir)]
        wanted = next((names for names in reversed(named) if isinstance(names, list)), None)
        cases = [case for case in all_cases if wanted is None or case.name in wanted]
        reports.append(report.format_session(session_dir, cases))
    emit("\n\n\n".join(reports))
    return EXIT_OK


def _add_run_parser(sub: Any) -> None:
    run = sub.add_parser("run", help="run the reviews and record the replies")
    run.add_argument("--cases", required=True, help="comma-separated case names, or 'all'")
    run.add_argument("--samples", type=int, default=1, help="repetitions of every cell; default 1")
    run.add_argument("--max-calls", type=int, help="hard cap on calls in the session; default the full plan's size")
    run.add_argument("--max-total-cost", type=float, default=DEFAULT_MAX_TOTAL_COST,
                     help=f"hard cap on dollars Claude reports in the session (default {DEFAULT_MAX_TOTAL_COST:g})")
    run.add_argument("--max-call-cost", type=float, default=clients.DEFAULT_MAX_CALL_COST,
                     help="dollar cap handed to each Claude call")
    run.add_argument("--timeout", type=int, default=clients.DEFAULT_TIMEOUT_SECONDS, help="wall-clock seconds per call")
    run.add_argument("--effort", help="reasoning effort for both clients (e.g. low, medium, high)")
    run.add_argument("--claude-model", help="model for the Claude reviewer; default the client's own")
    run.add_argument("--codex-model", help="model for the Codex reviewer; default the client's own")
    run.add_argument("--order-seed", type=int, help="seed for the cell order; default a fresh one, recorded")
    run.add_argument("--retry-errors", action="store_true", help="also repeat cells that ended in an error")
    run.add_argument("--dry-run", action="store_true", help="print the plan and the call count; start nothing")
    run.add_argument("--show-prompt", action="store_true", help="with --dry-run, also print one review prompt")
    run.add_argument("--results", default=str(DEFAULT_RESULTS), help="results root (outside any repository)")
    run.add_argument("--session", help="session folder under the results root; name an existing one to resume it")
    run.add_argument("--work-dir", help="where the empty per-call folders are made; default under the temp folder")
    run.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    run.set_defaults(func=cmd_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_reviewbench",
        description="Does a fresh reviewer from the other lab catch more seeded defects than the same model?")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    listing = sub.add_parser("list", help="list the cases")
    listing.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    listing.set_defaults(func=cmd_list)
    check = sub.add_parser("verify", help="check every case against its hidden tests and corrected version (no model calls)")
    check.add_argument("--cases", default=ALL)
    check.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    check.set_defaults(func=cmd_verify)
    summarise = sub.add_parser("summary", help="print the report for recorded sessions")
    summarise.add_argument("--results", default=str(DEFAULT_RESULTS), help="results root or one session folder")
    summarise.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    summarise.set_defaults(func=cmd_summary)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CaseError, TaskError, ValueError) as exc:
        sys.stderr.write(f"run_reviewbench: {exc}\n")
        return EXIT_USAGE
