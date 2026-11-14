"""Run one (task, arm, seed): a fresh workspace per client, scoring, and one JSON record.

A plain arm starts one client. A composite arm (best of two) starts each member in its own
workspace on a share of the budget and keeps one result, chosen without looking at the oracle."""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import arms, audit, duetinfo, layout, proc, scoring, usage
from .tasks import Task, prepare_workspace, workspace_diff

RECORD_VERSION = 2
SKILL_ROOT = Path(__file__).resolve().parents[2]
SUPERSEDED = "superseded"
KEPT_WORKSPACES = "workspaces"
EVIDENCE_TAIL_CHARS = 4000
in_git_repository = layout.in_git_repository


@dataclass(frozen=True)
class RunOptions:
    session_dir: Path                      # <results root>/<session id>
    timeout: int                           # a solo client's wall-clock limit; other arms derive theirs from it
    max_cost: Optional[float] = None
    effort: Optional[str] = None
    duet_turns: int = arms.DEFAULT_DUET_TURNS
    keep_workspaces: bool = False
    skill_root: Path = SKILL_ROOT          # the duet skill the hosts are pointed at (a staged copy in a real run)
    skill_source: Optional[Path] = None    # when set, `skill_root` is recopied from here before every duet run
    duet_home: Path = arms.default_duet_home()
    work_root: Optional[Path] = None       # where workspaces are made; default: under the temp folder
    duet_timeout: Optional[int] = None     # overrides the limit derived for duet arms
    watch: Tuple[Path, ...] = ()           # more folders no arm may read or sit near (results root, task source)


@dataclass(frozen=True)
class Attempt:
    """One client's run in one workspace, scored."""
    arm: str
    launch: arms.Launch
    done: proc.ProcResult
    used: usage.Usage
    duet: Optional[Dict[str, Any]]
    score: scoring.Score
    diff: str
    leaks: Dict[str, List[str]]
    infra: Optional[str]
    logs: Path
    kept_workspace: Optional[Path]
    timeout: int
    max_cost: Optional[float]


def run_id(task: str, arm: str, seed: int) -> str:
    return f"{task}__{arm}__s{seed}"


def session_limits(options: RunOptions) -> Dict[str, Any]:
    """The limits every run of a session shares; sessions are only comparable when these match."""
    return {"timeout": options.timeout,
            "duet_timeout": (options.duet_timeout if options.duet_timeout is not None
                             else arms.duet_timeout(options.timeout, options.duet_turns)),
            "max_cost": options.max_cost, "effort": options.effort, "duet_turns": options.duet_turns}


def kept_apart(options: RunOptions) -> Tuple[Path, ...]:
    """Folders no workspace may sit near and no Claude client may read."""
    return (options.session_dir.resolve(), *(path.resolve() for path in options.watch))


def work_root(options: RunOptions) -> Path:
    return options.work_root if options.work_root is not None else layout.default_work_root()


def budget_for(arm: str, options: RunOptions) -> Tuple[int, Optional[float]]:
    """(seconds, dollars) one client of this arm gets. A composite arm's members each get this."""
    return (arms.arm_timeout(arm, options.timeout, options.duet_turns, options.duet_timeout),
            arms.arm_max_cost(arm, options.max_cost))


def build_launch(task: Task, arm: str, workspace: Path, last_message: Path, options: RunOptions,
                 budget: Optional[Tuple[int, Optional[float]]] = None) -> arms.Launch:
    """The command and prompt for one client of a plain arm, on its own budget unless one is given."""
    timeout, max_cost = budget if budget is not None else budget_for(arm, options)
    context = arms.ArmContext(
        workspace=workspace, goal=task.goal, accept=task.accept, timeout=timeout,
        max_cost=max_cost, effort=options.effort, last_message=last_message,
        skill_root=options.skill_root, duet_turns=options.duet_turns, duet_home=options.duet_home,
        deny_read=kept_apart(options))
    return arms.ARMS[arm](context)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def diff_size(diff: str) -> int:
    """Lines a patch adds or removes."""
    return sum(1 for line in diff.splitlines()
               if line[:1] in "+-" and line[:1] and not line.startswith(("+++", "---")))


def _keep_duet_files(workspace: Path, duet: Optional[Dict[str, Any]], logs: Path) -> None:
    """Save what the duet left behind for later audit: the workspace's `.duet/` folder and, when the
    run lives elsewhere, a copy of that. Best-effort; a failed copy is noted."""
    sources = [(workspace / duetinfo.RUN_FOLDER, logs / "duet")]
    named = (duet or {}).get("run_folder")
    if named and workspace.resolve() not in Path(named).resolve().parents:
        sources.append((Path(named), logs / "duet-run"))
    for source, target in sources:
        if not source.is_dir():
            continue
        try:
            shutil.copytree(str(source), str(target), dirs_exist_ok=True)
        except (OSError, shutil.Error) as exc:
            (logs / f"{target.name}-copy-error.txt").write_text(str(exc), encoding="utf-8")


def _status(done: proc.ProcResult, used: usage.Usage) -> str:
    if done.timed_out:
        return "timeout"
    if done.exit_code == proc.EXIT_NOT_STARTED and done.error:
        return "not_started"
    if done.exit_code != 0 or used.reported_error:
        return "error"
    return "ok"


def _leaks(task: Task, logs: Path, options: RunOptions) -> Dict[str, List[str]]:
    sources = [logs / name for name in ("stdout.txt", "stderr.txt", "last_message.txt", "duet", "duet-run")]
    return {"oracle_paths_seen": audit.oracle_paths_seen(task, sources, kept_apart(options)),
            "outside_paths_seen": audit.outside_paths_seen(sources)}


def _dispose(run_dir: Path, workspace: Path, logs: Path, options: RunOptions) -> Optional[Path]:
    """Throw the workspace away, or move it beside the results when asked to keep it. Either way
    nothing is left in the work folder for the next arm to find."""
    kept: Optional[Path] = None
    if options.keep_workspaces:
        kept = options.session_dir / KEPT_WORKSPACES / logs.relative_to(options.session_dir / "logs") / layout.WORKSPACE_NAME
        if kept.parent.exists():
            shutil.rmtree(str(kept.parent))
        kept.parent.mkdir(parents=True)
        shutil.move(str(workspace), str(kept))
    shutil.rmtree(str(run_dir), ignore_errors=True)
    return kept


def attempt(task: Task, arm: str, logs: Path, options: RunOptions,
            budget: Tuple[int, Optional[float]]) -> Attempt:
    """Start one client of a plain arm in a fresh workspace, then score and audit what it left."""
    timeout, max_cost = budget
    logs.mkdir(parents=True)
    if arms.is_duet(arm) and options.skill_source is not None:
        layout.stage_skill(options.skill_source, options.skill_root)
    run_dir, workspace = layout.new_workspace(work_root(options))
    prepare_workspace(task, workspace)
    launch = build_launch(task, arm, workspace, logs / "last_message.txt", options, budget)
    (logs / "prompt.md").write_text(launch.prompt, encoding="utf-8")
    (logs / "command.json").write_text(json.dumps(launch.argv, indent=2), encoding="utf-8")
    done = proc.run(launch.argv, workspace, timeout, logs / "stdout.txt", logs / "stderr.txt",
                    stdin_text=launch.prompt, env=arms.env_for(launch.cli))
    output = _read(logs / "stdout.txt")
    used = (usage.parse_claude(output) if launch.cli == "claude"
            else usage.parse_codex(output, _read(logs / "last_message.txt")))
    duet = duetinfo.duet_outcome(workspace, used.final_message, options.duet_home) if launch.duet else None
    _keep_duet_files(workspace, duet, logs)
    diff = workspace_diff(workspace)
    (logs / "diff.patch").write_text(diff, encoding="utf-8")
    score = scoring.score_workspace(task, workspace, logs)
    evidence = _read(logs / "stderr.txt")[-EVIDENCE_TAIL_CHARS:] + "\n" + used.final_message[-EVIDENCE_TAIL_CHARS:]
    infra = audit.infra_failure(_status(done, used), bool(diff.strip()), used.parsed, evidence, duet)
    leaks = _leaks(task, logs, options)
    kept = _dispose(run_dir, workspace, logs, options)
    return Attempt(arm, launch, done, used, duet, score, diff, leaks, infra, logs, kept, timeout, max_cost)


def _cost_fields(tried: Attempt) -> Dict[str, Any]:
    """Dollars only where a client reported them; token counts kept apart because they differ in kind."""
    peer_cost = tried.duet.get("peer_cost_usd") if tried.duet else None
    parts = [value for value in (tried.used.cost_usd, peer_cost) if value is not None]
    return {
        "cost_usd": round(sum(parts), 6) if parts else None,
        "host_cost_usd": tried.used.cost_usd,
        "peer_cost_usd": peer_cost,
        "cost_note": tried.launch.cost_note,
        "tokens": dict(tried.used.tokens),
        "host_tokens": usage.total_tokens(tried.used.tokens),
        "peer_tokens": tried.duet.get("peer_tokens") if tried.duet else None,
        "token_basis": arms.token_basis(tried.launch),
    }


def attempt_fields(tried: Attempt, options: RunOptions) -> Dict[str, Any]:
    """Everything one client's run contributes to a record."""
    return {
        "cli": tried.launch.cli, "is_duet": tried.launch.duet,
        "status": _status(tried.done, tried.used), "exit_code": tried.done.exit_code,
        "timed_out": tried.done.timed_out, "error": tried.done.error, "seconds": round(tried.done.seconds, 2),
        "limits": {"timeout": tried.timeout, "solo_timeout": options.timeout, "max_cost": tried.max_cost,
                   "effort": options.effort, "effort_note": tried.launch.effort_note,
                   "duet_turns": options.duet_turns if tried.launch.duet else None},
        **_cost_fields(tried),
        "usage_parsed": tried.used.parsed, "session": tried.used.session,
        "final_message_tail": tried.used.final_message[-1500:],
        **tried.score.as_record(),
        "duet": tried.duet, "infra_failure": tried.infra,
        **tried.leaks,
        "logs": str(tried.logs),
        "workspace": str(tried.kept_workspace) if tried.kept_workspace else None,
    }


def pick_member(members: Sequence[Attempt]) -> Attempt:
    """The result a composite arm keeps: one that passes the visible check, then the one that came
    closest to it, then the smaller diff, then the earlier member. The oracle plays no part."""
    def rank(indexed: Tuple[int, Attempt]) -> Tuple[bool, int, int, int]:
        index, tried = indexed
        visible = tried.score.visible
        return (not visible.passed, -(visible.tests_passed or 0), diff_size(tried.diff), index)
    return min(enumerate(members), key=rank)[1]


def _first_not_ok(fields: Sequence[Dict[str, Any]]) -> str:
    return next((item["status"] for item in fields if item["status"] != "ok"), "ok")


def composite_fields(members: Sequence[Attempt], options: RunOptions, logs: Path) -> Dict[str, Any]:
    """A composite arm's record: the picked member's result, every member's spend, and each member in full."""
    picked = pick_member(members)
    each = [{"arm": tried.arm, **attempt_fields(tried, options)} for tried in members]
    costs = [item["cost_usd"] for item in each if item["cost_usd"] is not None]
    counted = [item["host_tokens"] for item in each if item["host_tokens"] is not None]
    infra = [f"{item['arm']}: {item['infra_failure']}" for item in each if item["infra_failure"]]
    tokens: Dict[str, int] = {}
    for tried in members:
        tokens = usage.add_tokens(tokens, tried.used.tokens)
    return {
        **attempt_fields(picked, options),
        "status": _first_not_ok(each), "timed_out": any(item["timed_out"] for item in each),
        "seconds": round(sum(item["seconds"] for item in each), 2),
        "cost_usd": round(sum(costs), 6) if costs else None, "peer_cost_usd": None,
        "cost_note": "dollars cover the members whose client reports them; Codex reports tokens only",
        "tokens": tokens, "host_tokens": sum(counted) if counted else None,
        "infra_failure": "; ".join(infra) or None,
        "oracle_paths_seen": sorted({hit for item in each for hit in item["oracle_paths_seen"]}),
        "outside_paths_seen": sorted({hit for item in each for hit in item["outside_paths_seen"]}),
        "logs": str(logs), "picked": picked.arm, "members": each,
    }


def _set_aside(session_dir: Path, name: str) -> None:
    """A rerun never overwrites: the earlier record and logs move to a `superseded` folder, numbered."""
    record, logs = session_dir / "records" / f"{name}.json", session_dir / "logs" / name
    if not record.exists() and not logs.exists():
        return
    number = 1
    while ((session_dir / "records" / SUPERSEDED / f"{name}.{number}.json").exists()
           or (session_dir / "logs" / SUPERSEDED / f"{name}.{number}").exists()):
        number += 1
    for source, target in ((record, session_dir / "records" / SUPERSEDED / f"{name}.{number}.json"),
                           (logs, session_dir / "logs" / SUPERSEDED / f"{name}.{number}")):
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))


def run_one(task: Task, arm: str, seed: int, options: RunOptions,
            order: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Run one arm on one task and write its record. Returns the record. `order` says where in its
    (task, seed) group this arm ran and from which seed the order came."""
    layout.check_apart(work_root(options), kept_apart(options))
    name = run_id(task.name, arm, seed)
    _set_aside(options.session_dir, name)
    logs = options.session_dir / "logs" / name
    budget = budget_for(arm, options)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if arm in arms.COMPOSITE_ARMS:
        members = [attempt(task, member, logs / member, options, budget) for member in arms.COMPOSITE_ARMS[arm]]
        fields = composite_fields(members, options, logs)
    else:
        fields = attempt_fields(attempt(task, arm, logs, options, budget), options)
    record: Dict[str, Any] = {
        "version": RECORD_VERSION, "id": name, "task": task.name, "arm": arm, "seed": seed,
        "eval_session": options.session_dir.name, "session_limits": session_limits(options),
        "order": order, "started_at": started_at, **fields,
    }
    records = options.session_dir / "records"
    records.mkdir(parents=True, exist_ok=True)
    (records / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
