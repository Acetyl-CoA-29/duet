"""Run one (task, arm, seed): fresh workspace, one headless client, then scoring and a JSON record."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import arms, duetinfo, proc, scoring, usage
from .tasks import Task, prepare_workspace, workspace_diff

RECORD_VERSION = 1
MAX_SCAN_BYTES = 5_000_000
SKILL_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RunOptions:
    session_dir: Path                      # <results root>/<session id>
    timeout: int
    max_cost: Optional[float] = None
    effort: Optional[str] = None
    duet_turns: int = arms.DEFAULT_DUET_TURNS
    keep_workspaces: bool = False
    skill_root: Path = SKILL_ROOT
    duet_home: Path = arms.default_duet_home()


def run_id(task: str, arm: str, seed: int) -> str:
    return f"{task}__{arm}__s{seed}"


def in_git_repository(path: Path) -> Optional[Path]:
    """The enclosing repository's root, if `path` (existing or not) would sit inside one."""
    current = path.resolve()
    for folder in (current, *current.parents):
        if (folder / ".git").exists():
            return folder
    return None


def build_launch(task: Task, arm: str, workspace: Path, last_message: Path, options: RunOptions) -> arms.Launch:
    context = arms.ArmContext(
        workspace=workspace, goal=task.goal, accept=task.accept, timeout=options.timeout,
        max_cost=options.max_cost, effort=options.effort, last_message=last_message,
        skill_root=options.skill_root, duet_turns=options.duet_turns, duet_home=options.duet_home)
    return arms.ARMS[arm](context)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def oracle_paths_seen(task: Task, sources: List[Path]) -> List[str]:
    """Audit, not prevention: which of the task's own folders are named in what the arm printed
    or wrote. The oracle is never in the workspace, but it does exist elsewhere on this disk."""
    watched = [str(task.root)]
    seen: List[str] = []
    for source in sources:
        files = [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.is_file()) if source.is_dir() else []
        for path in files:
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    continue
            except OSError:
                continue
            text = _read(path)
            seen.extend(f"{path.name}: {needle}" for needle in watched if needle in text)
    return sorted(set(seen))


def _keep_duet_files(workspace: Path, duet: Optional[Dict[str, Any]], logs: Path) -> None:
    """Save what the duet left behind for later audit: the workspace's `.duet/` folder and, when the
    status report names a run folder elsewhere, a copy of that. Best-effort; a failed copy is noted."""
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


def _cost_fields(launch: arms.Launch, used: usage.Usage, duet: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    peer_cost = duet.get("peer_cost_usd") if duet else None
    parts = [value for value in (used.cost_usd, peer_cost) if value is not None]
    return {
        "cost_usd": round(sum(parts), 6) if parts else None,
        "host_cost_usd": used.cost_usd,
        "peer_cost_usd": peer_cost,
        "tokens": dict(used.tokens),
        "peer_tokens": duet.get("peer_tokens") if duet else None,
        "cost_note": launch.cost_note,
    }


def _status(done: proc.ProcResult, used: usage.Usage) -> str:
    if done.timed_out:
        return "timeout"
    if done.exit_code == proc.EXIT_NOT_STARTED and done.error:
        return "not_started"
    if done.exit_code != 0 or used.reported_error:
        return "error"
    return "ok"


def run_one(task: Task, arm: str, seed: int, options: RunOptions) -> Dict[str, Any]:
    """Run one arm on one task in a throwaway workspace and write its record. Returns the record."""
    name = run_id(task.name, arm, seed)
    logs = options.session_dir / "logs" / name
    if logs.exists():
        shutil.rmtree(str(logs))
    logs.mkdir(parents=True)
    work = options.session_dir / "work" / uuid.uuid4().hex[:12]   # opaque: the path names no task
    workspace = work / "ws"
    prepare_workspace(task, workspace)
    launch = build_launch(task, arm, workspace, logs / "last_message.txt", options)
    (logs / "prompt.md").write_text(launch.prompt, encoding="utf-8")
    (logs / "command.json").write_text(json.dumps(launch.argv, indent=2), encoding="utf-8")

    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    done = proc.run(launch.argv, workspace, options.timeout, logs / "stdout.txt", logs / "stderr.txt",
                    stdin_text=launch.prompt, env=arms.env_for(launch.cli))
    output = _read(logs / "stdout.txt")
    used = (usage.parse_claude(output) if launch.cli == "claude"
            else usage.parse_codex(output, _read(logs / "last_message.txt")))

    duet = duetinfo.duet_outcome(workspace, used.final_message) if launch.duet else None
    _keep_duet_files(workspace, duet, logs)
    (logs / "diff.patch").write_text(workspace_diff(workspace), encoding="utf-8")
    score = scoring.score_workspace(task, workspace, logs)

    record: Dict[str, Any] = {
        "version": RECORD_VERSION, "id": name, "task": task.name, "arm": arm, "seed": seed,
        "cli": launch.cli, "is_duet": launch.duet, "started_at": started_at,
        "status": _status(done, used), "exit_code": done.exit_code, "timed_out": done.timed_out,
        "error": done.error, "seconds": round(done.seconds, 2),
        "limits": {"timeout": options.timeout, "max_cost": options.max_cost, "effort": options.effort,
                   "duet_turns": options.duet_turns if launch.duet else None},
        **_cost_fields(launch, used, duet),
        "usage_parsed": used.parsed, "session": used.session,
        "final_message_tail": used.final_message[-1500:],
        **score.as_record(),
        "duet": duet,
        "oracle_paths_seen": oracle_paths_seen(task, [logs / "stdout.txt", logs / "last_message.txt", logs / "duet", logs / "duet-run"]),
        "logs": str(logs),
        "workspace": str(workspace) if options.keep_workspaces else None,
    }
    records = options.session_dir / "records"
    records.mkdir(parents=True, exist_ok=True)
    (records / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if not options.keep_workspaces:
        shutil.rmtree(str(work), ignore_errors=True)
    return record
