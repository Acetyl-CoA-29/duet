"""Talk to the other model through its own command-line client, keeping one session alive."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import models
from .gates import stop_group

DEFAULT_TIMEOUT_SECONDS = 2400
ERROR_TAIL_CHARS = 1500
KILL_GRACE_SECONDS = 5
SNAPSHOT_ENV = "DUET_SNAPSHOT"
BIN_ENV = {"codex": "DUET_CODEX_BIN", "claude": "DUET_CLAUDE_BIN"}
OTHER_LAB_SECRETS = {
    "codex": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    "claude": ("OPENAI_API_KEY", "CODEX_API_KEY"),
}
COMMON_SECRETS = ("OPENROUTER_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "AWS_SECRET_ACCESS_KEY",
                  "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "NPM_TOKEN", "PYPI_TOKEN", "HF_TOKEN")
SECRET_SUFFIXES = ("_TOKEN", "_SECRET", "_API_KEY", "_PASSWORD")  # dropped by name unless the client's own
# Names each client reads to sign in or reach its own service; these stay even though they look secret.
OWN_PREFIXES = {"codex": ("OPENAI_", "CODEX_"), "claude": ("ANTHROPIC_", "CLAUDE_")}
BEDROCK_SWITCH = "CLAUDE_CODE_USE_BEDROCK"  # a Claude client on Bedrock signs in with AWS credentials
BEDROCK_NAMES = ("AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK")
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")  # never option-like: it goes on a command line
BAD_SESSION = ("the peer client reported a session id that cannot be used safely on a command line; "
               "the reply was discarded and no session was kept")
CLAUDE_FOLDER = ".claude"
CLAUDE_EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")


def host_is_sandboxed() -> bool:
    """True when this process was started by a Codex host inside its command sandbox."""
    return bool(os.environ.get("CODEX_SANDBOX"))


def peer_binary(peer: str) -> str:
    return os.environ.get(BIN_ENV[peer], peer)


def launch_problem(peer: str) -> Optional[str]:
    """Explain up front why the peer cannot be reached from here, if that is the case."""
    if shutil.which(peer_binary(peer)) is None:
        return f"the `{peer_binary(peer)}` command-line client was not found; install it and sign in first"
    if peer == "claude" and os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1":
        return ("the Codex sandbox this command runs in has no network access, so the Claude client cannot "
                "be reached. Start Codex with network access and a writable ~/.claude, for example: "
                "codex -c 'sandbox_workspace_write.network_access=true' --add-dir ~/.claude "
                "--add-dir ~/.local/state/duet")
    return None


@dataclass(frozen=True)
class PeerResult:
    ok: bool
    reply: str
    session: Optional[str]
    error: str = ""
    usage: Optional[Dict[str, Any]] = None
    model: Optional[str] = None


def _codex_command(session: Optional[str], write: bool, model: Optional[str], effort: Optional[str], last: Path) -> List[str]:
    sandbox = "workspace-write" if write else "read-only"
    command = [peer_binary("codex"), "exec"]
    if session:
        command.append("resume")
    command += ["--skip-git-repo-check", "--json", "-o", str(last), "-c", f'sandbox_mode="{sandbox}"']
    if model:
        command += ["-m", model]
    if effort:
        command += ["-c", f'model_reasoning_effort="{effort}"']
    if session:
        command.append(session)
    return command + ["-"]


def enforces_read_only(peer: str) -> bool:
    """True when the peer's own client refuses writes at the operating-system level on a read-only turn,
    so the runner need not copy the workspace first (it still compares before and after).

    Only the real Codex client qualifies: its `read-only` sandbox confines every command the model runs.
    A substituted binary (DUET_CODEX_BIN) promises nothing, and Claude's read-only turns rest on settings
    or plan mode. DUET_SNAPSHOT=always keeps the copy regardless."""
    if os.environ.get(SNAPSHOT_ENV, "").strip().lower() == "always":
        return False
    return peer == "codex" and not os.environ.get(BIN_ENV["codex"])


def claude_uses_plan_mode(write: bool) -> bool:
    """Plan mode is the read-only guard only where Claude's own sandbox cannot run (inside a Codex sandbox)."""
    return not write and host_is_sandboxed()


def claude_settings(workspace: Path, write: bool, hidden: Optional[Path]) -> str:
    """Per-turn settings for a Claude peer: its file tools may not read the run store, and, when its own
    command sandbox can start, commands get no network, no route around the sandbox, no view of the run
    store and, on a read-only turn, no write access to the workspace."""
    settings: Dict[str, Any] = {}
    if hidden is not None:
        settings["permissions"] = {"deny": [f"Read(/{hidden}/**)", f"Edit(/{hidden}/**)"]}
    if not host_is_sandboxed():
        files: Dict[str, List[str]] = {}
        if hidden is not None:
            files["denyRead"] = [str(hidden)]
        if not write:
            files["denyWrite"] = [str(workspace.resolve())]
        settings["sandbox"] = {"enabled": True, "autoAllowBashIfSandboxed": True,
                               "allowUnsandboxedCommands": False, "filesystem": files}
    return json.dumps(settings)


def _claude_command(session: Optional[str], write: bool, model: Optional[str], user_config: bool,
                    workspace: Path, hidden: Optional[Path]) -> List[str]:
    """Claude as peer. Build turns: edit mode. Read-only turns: its edit tools are switched off and its
    command sandbox denies writes to the workspace; inside a Codex sandbox, where Claude's own sandbox
    cannot start, plan mode stands in and the host's sandbox confines commands."""
    command = [peer_binary("claude"), "-p", "--output-format", "json"]
    if write:
        command += ["--permission-mode", "acceptEdits"]
    elif claude_uses_plan_mode(write):
        command += ["--permission-mode", "plan"]
    else:
        command += ["--disallowedTools", *CLAUDE_EDIT_TOOLS]
    if host_is_sandboxed():
        command += ["--allowedTools", "Bash"]
    command += ["--settings", claude_settings(workspace, write, hidden)]
    if not user_config:
        command.append("--safe-mode")
    if model:
        command += ["--model", model]
    if session:
        command += ["--resume", session]
    return command


def _count(value: Any) -> int:
    """A token count from client output; anything that is not a plain number counts as zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        return max(0, int(value))
    except (ValueError, OverflowError):
        return 0


def usable_session(value: Any) -> bool:
    return isinstance(value, str) and bool(SESSION_RE.match(value))


def _token_usage(raw: Any) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    return {"input_tokens": _count(data.get("input_tokens")),
            "output_tokens": _count(data.get("output_tokens"))}


def _parse_codex(events: str, last: Path) -> PeerResult:
    session: Optional[str] = None
    replies: List[str] = []
    usage: Dict[str, Any] = {}
    completed = False
    error = "the peer stream ended without a completed turn"
    # JSONL uses LF, not the Unicode separators which are legal inside JSON strings.
    for line in events.split("\n"):
        if not line.strip():
            continue
        completed = False
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            session = event.get("thread_id") or session
        completed = event.get("type") == "turn.completed"
        if event.get("type") in ("turn.completed", "turn.failed") and isinstance(event.get("usage"), dict):
            usage = _token_usage(event["usage"])
        if event.get("type") == "turn.failed":
            detail = event.get("error")
            if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                error = "the peer turn failed: " + detail["message"]
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if (event.get("type") == "item.completed" and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)):
            replies.append(item["text"])
    if not completed:
        return PeerResult(False, "", session, error, usage)
    try:
        reply = last.read_text(encoding="utf-8", errors="replace") if last.is_file() else ""
    except OSError:
        reply = ""
    if not reply.strip() and replies:
        reply = replies[-1]
    return PeerResult(ok=bool(reply.strip()), reply=reply.strip(), session=session,
                      error="" if reply.strip() else "the peer returned no message", usage=usage)


def _parse_claude(output: str) -> PeerResult:
    data: Any = None
    candidates = [output.strip()] + list(reversed(output.strip().split("\n")))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and ("result" in data or data.get("type") == "result"):
            break
    else:
        return PeerResult(False, "", None, "the peer's output was not JSON")
    if not isinstance(data, dict):
        return PeerResult(False, "", None, "the peer's output was not a JSON object")
    reply = data["result"].strip() if isinstance(data.get("result"), str) else ""
    cost = data.get("total_cost_usd")
    valid_cost = isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0
    # A huge JSON integer must not crash recovery or turn a reported bill into zero spend.
    spent = float(min(cost, sys.float_info.max)) if valid_cost else 0.0
    usage = {**_token_usage(data.get("usage")), "cost_usd": spent}
    model = models.reported_by_claude(data)
    if data.get("is_error") or not reply:
        return PeerResult(False, "", data.get("session_id"), reply or "the peer reported an error", usage, model)
    return PeerResult(True, reply, data.get("session_id"), usage=usage, model=model)


def saved_result(peer: str, turn_dir: Path) -> PeerResult:
    """Parse a saved receipt, including usage from a process interrupted before it was journaled."""
    try:
        output = (turn_dir / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        output = ""
    try:
        return _parse_codex(output, turn_dir / "last.txt") if peer == "codex" else _parse_claude(output)
    except (ValueError, OverflowError, RecursionError):
        return PeerResult(False, "", None, "the peer's saved output could not be parsed")


def _drop_empty_client_folder(workspace: Path) -> None:
    """Claude's sandbox leaves an empty `.claude/.cc-writes` behind; remove it when nothing else is there."""
    for folder in (workspace / CLAUDE_FOLDER / ".cc-writes", workspace / CLAUDE_FOLDER):
        try:
            folder.rmdir()
        except OSError:
            return


def peer_env(peer: str) -> Dict[str, str]:
    """The peer's environment without the other lab's credentials, common deploy secrets, or anything
    whose name ends like a secret. Kept: what the peer's own client signs in with (its lab's prefixes,
    AWS credentials for a Claude client on Bedrock) and whatever DUET_KEEP_ENV lists."""
    keep = {name.strip() for name in os.environ.get("DUET_KEEP_ENV", "").split(",") if name.strip()}
    if peer == "claude" and os.environ.get(BEDROCK_SWITCH):
        keep |= set(BEDROCK_NAMES)
    listed = set(OTHER_LAB_SECRETS[peer]) | set(COMMON_SECRETS)

    def dropped(name: str) -> bool:
        if name in keep:
            return False
        if name in listed:
            return True
        return name.upper().endswith(SECRET_SUFFIXES) and not name.upper().startswith(OWN_PREFIXES[peer])

    return {name: value for name, value in os.environ.items() if not dropped(name)}


def run_turn(
    peer: str,
    prompt: str,
    workspace: Path,
    turn_dir: Path,
    session: Optional[str],
    write: bool,
    model: Optional[str],
    timeout: int,
    effort: Optional[str] = None,
    user_config: bool = False,
    hidden: Optional[Path] = None,
) -> PeerResult:
    """Send one message to the peer and wait for its reply. The prompt travels on stdin only."""
    turn_dir.mkdir(parents=True, exist_ok=True)
    if session is not None and not usable_session(session):
        return PeerResult(False, "", None, BAD_SESSION)
    last, out_path, err_path = turn_dir / "last.txt", turn_dir / "stdout.txt", turn_dir / "stderr.txt"
    (turn_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    command = _codex_command(session, write, model, effort, last) if peer == "codex" else _claude_command(session, write, model, user_config, workspace, hidden)
    timed_out, code = False, None
    had_client_folder = (workspace / CLAUDE_FOLDER).exists()
    try:
        with out_path.open("wb") as out, err_path.open("wb") as err:
            # Own process group, so a timeout stops the client and everything it started.
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                     cwd=str(workspace), start_new_session=True, env=peer_env(peer))
            try:
                child.communicate(prompt.encode("utf-8"), timeout=timeout)
                code = child.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                stop_group(child, KILL_GRACE_SECONDS)
            except BaseException:
                # The runner itself is being stopped (a signal, Ctrl-C): the peer must not outlive it.
                stop_group(child, KILL_GRACE_SECONDS)
                raise
    except OSError as exc:
        return PeerResult(False, "", session, f"could not start {command[0]}: {exc}")
    if peer == "claude" and not had_client_folder:
        _drop_empty_client_folder(workspace)
    parsed = saved_result(peer, turn_dir)
    if parsed.session is not None and not usable_session(parsed.session):
        return PeerResult(False, "", session, BAD_SESSION, parsed.usage)
    kept = parsed.session or session
    if timed_out:
        return PeerResult(False, "", kept, f"the peer timed out after {timeout}s; its work may be partly done", parsed.usage)
    if code != 0:
        tail = err_path.read_text(encoding="utf-8", errors="replace")[-ERROR_TAIL_CHARS:].strip()
        return PeerResult(False, "", kept, f"the peer exited with code {code}: {tail or parsed.error}", parsed.usage)
    if parsed.ok and not kept:
        return PeerResult(False, "", None, "the peer replied without a session id, so the conversation "
                                           "could not be continued; its reply was discarded", parsed.usage)
    return PeerResult(parsed.ok, parsed.reply, kept, parsed.error, parsed.usage, parsed.model)
