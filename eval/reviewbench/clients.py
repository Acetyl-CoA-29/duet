"""Start one reviewer call and read back its reply, usage and cost.

Both clients are started read-only, in an empty folder, with the prompt on standard input:
  Claude: `--tools ""` (no tools at all), `--safe-mode` (no personal hooks, instructions or
          plugins), reading the cases and the results denied by rule, `--max-budget-usd` per call.
  Codex:  `read-only` sandbox. It keeps a shell that can read the disk, and runs with the user's
          own Codex configuration; every call's output is searched for signs that it went looking.
A second message to the same session (the `self` condition) resumes it by id."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from evallib import arms, proc, usage

from .cases import HIDDEN_FILES

CLAUDE, CODEX = "claude", "codex"
REVIEWERS = (CLAUDE, CODEX)
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_CALL_COST = 0.5
ERROR_TAIL_CHARS = 500
COMMAND_MARK = '"command_execution"'     # how `codex exec --json` labels a shell command it ran


@dataclass(frozen=True)
class ClientOptions:
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    max_call_cost: Optional[float] = DEFAULT_MAX_CALL_COST   # Claude only; Codex reports no dollars
    effort: Optional[str] = None
    claude_model: Optional[str] = None
    codex_model: Optional[str] = None
    deny_read: Tuple[Path, ...] = ()      # folders a reviewer must not read: the cases, the results


@dataclass(frozen=True)
class CallResult:
    ok: bool
    reply: str
    session: Optional[str]
    cost_usd: Optional[float]
    tokens: Dict[str, int]
    seconds: float
    exit_code: Optional[int]
    timed_out: bool
    error: str = ""
    leaks: Tuple[str, ...] = ()           # hidden-material names found in what the client printed
    commands_run: int = 0                 # shell commands a Codex reviewer ran despite being told not to


def other(reviewer: str) -> str:
    return CODEX if reviewer == CLAUDE else CLAUDE


def claude_argv(options: ClientOptions, session: Optional[str] = None) -> List[str]:
    settings = {"permissions": {"deny": [f"Read(/{path}/**)" for path in options.deny_read]}}
    argv = [arms.claude_bin(), "-p", "--output-format", "json", "--safe-mode", "--tools", "",
            "--settings", json.dumps(settings)]
    if options.max_call_cost is not None:
        argv += ["--max-budget-usd", f"{options.max_call_cost:g}"]
    if options.claude_model:
        argv += ["--model", options.claude_model]
    if options.effort:
        argv += ["--effort", options.effort]
    if session:
        argv += ["--resume", session]
    return argv


def codex_argv(options: ClientOptions, last_message: Path, session: Optional[str] = None) -> List[str]:
    argv = [arms.codex_bin(), "exec", *(["resume"] if session else []),
            "--skip-git-repo-check", "--json", "-o", str(last_message), "-c", 'sandbox_mode="read-only"']
    if options.codex_model:
        argv += ["-m", options.codex_model]
    if options.effort:
        argv += ["-c", f'model_reasoning_effort="{options.effort}"']
    return argv + ([session] if session else []) + ["-"]


def build_argv(reviewer: str, options: ClientOptions, last_message: Path,
               session: Optional[str] = None) -> List[str]:
    if reviewer == CLAUDE:
        return claude_argv(options, session)
    if reviewer == CODEX:
        return codex_argv(options, last_message, session)
    raise ValueError(f"unknown reviewer: {reviewer!r}")


def leak_needles(options: ClientOptions) -> List[str]:
    """Names that have no business in a reviewer's output: the hidden files and the denied folders."""
    return [*HIDDEN_FILES, *(str(path) for path in options.deny_read)]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _failure(done: proc.ProcResult, used: usage.Usage, reply: str, stderr: str) -> str:
    """Why this call produced no usable reply; empty when it did."""
    if done.error:
        return done.error
    if done.exit_code != 0:
        return f"the client exited with code {done.exit_code}: {stderr.strip()[-ERROR_TAIL_CHARS:]}"
    if not used.parsed:
        return "the client's output held no result"
    if used.reported_error:
        return f"the client reported an error: {reply.strip()[-ERROR_TAIL_CHARS:]}"
    return "" if reply.strip() else "the client returned an empty reply"


def call(reviewer: str, prompt: str, workdir: Path, logs: Path, step: str, options: ClientOptions,
         session: Optional[str] = None) -> CallResult:
    """One paid call. The prompt, command and raw output are kept under `logs`; the environment is
    never written anywhere."""
    logs.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    last_message = logs / f"{step}.last_message.txt"
    argv = build_argv(reviewer, options, last_message, session)
    (logs / f"{step}.prompt.md").write_text(prompt, encoding="utf-8")
    (logs / f"{step}.command.json").write_text(json.dumps({"argv": argv, "cwd": str(workdir)}, indent=2) + "\n",
                                               encoding="utf-8")
    out, err = logs / f"{step}.stdout.txt", logs / f"{step}.stderr.txt"
    done = proc.run(argv, workdir, options.timeout, out, err, stdin_text=prompt, env=arms.env_for(reviewer))
    stdout, stderr = _read(out), _read(err)
    used = usage.parse_claude(stdout) if reviewer == CLAUDE else usage.parse_codex(stdout, _read(last_message))
    error = _failure(done, used, used.final_message, stderr)
    printed = stdout + stderr
    return CallResult(
        ok=not error, reply=used.final_message, session=used.session, cost_usd=used.cost_usd,
        tokens=dict(used.tokens), seconds=round(done.seconds, 2), exit_code=done.exit_code,
        timed_out=done.timed_out, error=error,
        leaks=tuple(needle for needle in leak_needles(options) if needle in printed),
        commands_run=printed.count(COMMAND_MARK) if reviewer == CODEX else 0)
