"""The arms being compared. Each arm is a function from a run's context to the exact command and
the prompt that goes to it on stdin. Add an arm by writing a builder and registering it in ARMS."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

CLAUDE_BIN_ENV = "DUET_EVAL_CLAUDE_BIN"
CODEX_BIN_ENV = "DUET_EVAL_CODEX_BIN"
# Never handed to a client of the other lab; the common deploy secrets are dropped for both.
OTHER_LAB_SECRETS = {"claude": ("OPENAI_API_KEY", "OPENAI_ORG_ID"),
                     "codex": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
COMMON_SECRETS = ("OPENROUTER_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY",
                  "AWS_SESSION_TOKEN", "NPM_TOKEN", "PYPI_TOKEN", "HF_TOKEN")
DEFAULT_DUET_TURNS = 8
# A Claude host's shell tool stops a command at ten minutes; a peer turn must end before that.
MAX_PEER_TURN_SECONDS = 540


@dataclass(frozen=True)
class ArmContext:
    workspace: Path
    goal: str
    accept: str
    timeout: int
    max_cost: Optional[float]
    effort: Optional[str]
    last_message: Path           # where Codex writes its final message
    skill_root: Path             # the duet skill checkout the host is pointed at
    duet_turns: int = DEFAULT_DUET_TURNS
    claude_home: Path = Path.home() / ".claude"
    duet_home: Path = Path.home() / ".local" / "state" / "duet"   # where the runner keeps its record


@dataclass(frozen=True)
class Launch:
    argv: List[str]
    prompt: str
    cli: str                     # "claude" or "codex": whose output format and key rules apply
    duet: bool = False
    cost_note: str = ""


def default_duet_home() -> Path:
    """The duet runner's state folder: `DUET_HOME`, or its documented default."""
    return Path(os.environ.get("DUET_HOME") or Path.home() / ".local" / "state" / "duet").expanduser()


def claude_bin() -> str:
    return os.environ.get(CLAUDE_BIN_ENV, "claude")


def codex_bin() -> str:
    return os.environ.get(CODEX_BIN_ENV, "codex")


def env_for(cli: str, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The client's environment without the other lab's key or common deploy secrets."""
    source = dict(os.environ if base is None else base)
    drop = set(OTHER_LAB_SECRETS[cli]) | set(COMMON_SECRETS)
    return {name: value for name, value in source.items() if name not in drop}


def peer_turn_seconds(ctx: ArmContext) -> int:
    """The limit a host is told to give each peer turn: long enough for a build turn, shorter than
    the run's own limit and than a Claude host's shell tool allows."""
    return max(1, min(MAX_PEER_TURN_SECONDS, ctx.timeout))


def task_brief(ctx: ArmContext) -> str:
    """The part of the prompt that is word-for-word the same for every arm."""
    return (
        "## Task\n\n" + ctx.goal.strip() + "\n\n"
        "## Acceptance command\n\n"
        f"Run from the workspace root; it must exit 0:\n\n    {ctx.accept}\n\n"
        "## Rules\n\n"
        "- The workspace is the current directory. Everything you need is in it; do not read or "
        "write anything outside it except where these instructions name a path.\n"
        "- Do not edit, delete or weaken the existing tests or the acceptance command.\n"
        "- The finished work is judged against the whole task description, not only the "
        "acceptance command.\n"
    )


def solo_prompt(ctx: ArmContext) -> str:
    return (
        "You are working alone on a small coding task. Nobody will answer questions, so make "
        "reasonable decisions and finish the job.\n\n" + task_brief(ctx) + "\n"
        "## How to work\n\n"
        "1. Read the task and the existing files.\n"
        "2. Make the change, then run the acceptance command. Iterate until it exits 0.\n"
        "3. Review your own work before stopping: reread the task description line by line, "
        "check each requirement against the code, and try at least one case the tests do not "
        "cover. Fix what you find and rerun the acceptance command.\n"
        "4. End with one short paragraph: what you changed and the acceptance command's final result.\n"
    )


def duet_prompt(ctx: ArmContext, host: str) -> str:
    peer = "Codex" if host == "claude" else "Claude"
    runner = ctx.skill_root / "scripts" / "duet.py"
    flags = f"--max-turns {ctx.duet_turns}"
    if host == "codex":
        flags = "--peer claude " + flags
        if ctx.max_cost is not None:
            flags += f" --max-cost {ctx.max_cost:g}"
    elif ctx.effort:
        flags += f" --peer-effort {ctx.effort}"
    return (
        f"Run a duet. You are the host; {peer} is your peer. Nobody will answer questions: the "
        "user has already agreed to the two models, the peer's write access to this workspace "
        "and the budget below.\n\n"
        f"Read the skill instructions at {ctx.skill_root / 'SKILL.md'} first and follow them "
        f"exactly, passing these options to `init`: {flags}\n\n"
        f"Call the runner as `python3 {runner} <command> ...`, one runner command per shell "
        "call, starting with exactly those two words, with no `cd`, variables, pipes or `&&` "
        f"around it. Pass `--timeout {peer_turn_seconds(ctx)}` to every `peer` command and let "
        "that shell call wait at least that long plus a minute; never use a shorter timeout.\n\n"
        + task_brief(ctx) + "\n"
        "Use the task above as the duet's goal and the acceptance command above as its "
        "acceptance command.\n\n"
        "When a duet command fails, do what the skill instructions say for that exit code. If "
        "they do not cover it, or the retry they allow also fails, report the exact error text "
        "and exit code and stop. Never work around the runner and never continue alone. End by "
        "printing the complete output of the runner's `status` command.\n"
    )


def _claude_settings(ctx: Optional[ArmContext] = None, unsandboxed_prefix: Optional[str] = None) -> str:
    """Shell commands run in Claude's own sandbox (no network) without approval prompts. A duet
    host additionally runs the duet runner, and only that, outside the sandbox, and may write its
    message drafts into the runner's state folder."""
    sandbox: Dict[str, object] = {"enabled": True, "autoAllowBashIfSandboxed": True}
    if ctx is not None and unsandboxed_prefix:
        sandbox = {**sandbox,
                   "excludedCommands": [f"{unsandboxed_prefix} *", f"{unsandboxed_prefix}:*"],
                   "filesystem": {"allowWrite": [str(ctx.duet_home)]}}
    return json.dumps({"sandbox": sandbox})


def claude_host_rules(ctx: ArmContext, runner_prefix: str) -> List[str]:
    """Everything a headless Claude host may do beyond editing the workspace and running sandboxed
    commands: call the duet runner, read the skill's instructions, and use the runner's state folder."""
    return [f"Bash({runner_prefix} *)", f"Read(/{ctx.skill_root / 'SKILL.md'})",
            f"Read(/{ctx.duet_home}/**)", f"Edit(/{ctx.duet_home}/**)"]


def _claude_argv(ctx: ArmContext, runner_prefix: Optional[str] = None) -> List[str]:
    """Clean session (no personal hooks, instructions or plugins), edits accepted, a full event
    stream so the run can be audited afterwards."""
    argv = [claude_bin(), "-p", "--output-format", "stream-json", "--verbose", "--safe-mode",
            "--permission-mode", "acceptEdits", "--settings", _claude_settings(ctx, runner_prefix)]
    if runner_prefix:
        argv += ["--allowedTools", *claude_host_rules(ctx, runner_prefix)]
    if ctx.max_cost is not None:
        argv += ["--max-budget-usd", f"{ctx.max_cost:g}"]
    if ctx.effort:
        argv += ["--effort", ctx.effort]
    return argv


def _codex_argv(ctx: ArmContext, reach_claude: bool = False) -> List[str]:
    argv = [codex_bin(), "exec", "--skip-git-repo-check", "--json", "-o", str(ctx.last_message),
            "-c", 'sandbox_mode="workspace-write"']
    if ctx.effort:
        argv += ["-c", f'model_reasoning_effort="{ctx.effort}"']
    if reach_claude:
        # The Claude client needs the network and its own folder (see the skill's notes for a Codex host).
        argv += ["-c", "sandbox_workspace_write.network_access=true", "--add-dir", str(ctx.claude_home)]
        if ctx.duet_home.is_dir():
            # Lets the runner keep its record outside the workspace; without it the skill falls
            # back to a record inside the workspace.
            argv += ["--add-dir", str(ctx.duet_home)]
    return argv + ["-"]


CODEX_COST_NOTE = "Codex reports tokens, not dollars; only the wall-clock limit caps it"


def solo_claude(ctx: ArmContext) -> Launch:
    return Launch(_claude_argv(ctx), solo_prompt(ctx), "claude")


def solo_codex(ctx: ArmContext) -> Launch:
    return Launch(_codex_argv(ctx), solo_prompt(ctx), "codex", cost_note=CODEX_COST_NOTE)


def duet_claude_host(ctx: ArmContext) -> Launch:
    prefix = f"python3 {ctx.skill_root / 'scripts' / 'duet.py'}"
    return Launch(_claude_argv(ctx, prefix), duet_prompt(ctx, "claude"), "claude", duet=True,
                  cost_note="dollars cover the Claude host only; the Codex peer reports tokens")


def duet_codex_host(ctx: ArmContext) -> Launch:
    return Launch(_codex_argv(ctx, reach_claude=True), duet_prompt(ctx, "codex"), "codex", duet=True,
                  cost_note="dollars cover the Claude peer only (from the duet status); the Codex host reports tokens")


ArmBuilder = Callable[[ArmContext], Launch]

ARMS: Dict[str, ArmBuilder] = {
    "solo-claude": solo_claude,
    "solo-codex": solo_codex,
    "duet-claude-host": duet_claude_host,
    "duet-codex-host": duet_codex_host,
}
