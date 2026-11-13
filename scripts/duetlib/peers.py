"""Talk to the other model through its own command-line client, keeping one session alive."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

DEFAULT_TIMEOUT_SECONDS = 2400
ERROR_TAIL_CHARS = 1500
CLAUDE_SANDBOX_SETTINGS = json.dumps({"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}})


def host_is_sandboxed() -> bool:
    """True when this process was started by a Codex host inside its command sandbox."""
    return bool(os.environ.get("CODEX_SANDBOX"))


def launch_problem(peer: str) -> Optional[str]:
    """Explain up front why the peer cannot be reached from here, if that is the case."""
    if peer == "claude" and os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1":
        return ("the Codex sandbox this command runs in has no network access, so the Claude client cannot "
                "be reached. Start Codex with network access and a writable ~/.claude, for example: "
                "codex -c 'sandbox_workspace_write.network_access=true' --add-dir ~/.claude")
    return None


@dataclass(frozen=True)
class PeerResult:
    ok: bool
    reply: str
    session: Optional[str]
    error: str = ""


def _codex_command(session: Optional[str], write: bool, model: Optional[str], effort: Optional[str], last: Path) -> List[str]:
    sandbox = "workspace-write" if write else "read-only"
    command = [os.environ.get("DUET_CODEX_BIN", "codex"), "exec"]
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


def _claude_command(session: Optional[str], write: bool, model: Optional[str], user_config: bool) -> List[str]:
    """Claude as peer: plan mode for read-only turns, edit mode for build turns, and its own
    command sandbox in both so it can run tests without network or approval prompts."""
    command = [
        os.environ.get("DUET_CLAUDE_BIN", "claude"), "-p", "--output-format", "json",
        "--permission-mode", "acceptEdits" if write else "plan",
    ]
    if host_is_sandboxed():
        # Claude's own sandbox cannot start inside the host's; the host's sandbox already confines commands.
        command += ["--allowedTools", "Bash"]
    else:
        command += ["--settings", CLAUDE_SANDBOX_SETTINGS]
    if not user_config:
        command.append("--safe-mode")
    if model:
        command += ["--model", model]
    if session:
        command += ["--resume", session]
    return command


def _parse_codex(events: str, last: Path) -> PeerResult:
    session: Optional[str] = None
    replies: List[str] = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            session = event.get("thread_id") or session
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            replies.append(item.get("text", ""))
    reply = last.read_text(encoding="utf-8") if last.is_file() else ""
    if not reply.strip() and replies:
        reply = replies[-1]
    return PeerResult(ok=bool(reply.strip()), reply=reply.strip(), session=session,
                      error="" if reply.strip() else "the peer returned no message")


def _parse_claude(output: str) -> PeerResult:
    try:
        data = json.loads(output.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return PeerResult(False, "", None, "the peer's output was not JSON")
    if not isinstance(data, dict):
        return PeerResult(False, "", None, "the peer's output was not a JSON object")
    reply = str(data.get("result") or "").strip()
    if data.get("is_error") or not reply:
        return PeerResult(False, "", data.get("session_id"), reply or "the peer reported an error")
    return PeerResult(True, reply, data.get("session_id"))


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
) -> PeerResult:
    """Send one message to the peer and wait for its reply. The prompt travels on stdin only."""
    turn_dir.mkdir(parents=True, exist_ok=True)
    last, out_path, err_path = turn_dir / "last.txt", turn_dir / "stdout.txt", turn_dir / "stderr.txt"
    (turn_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    command = _codex_command(session, write, model, effort, last) if peer == "codex" else _claude_command(session, write, model, user_config)
    timed_out, code = False, None
    try:
        with out_path.open("wb") as out, err_path.open("wb") as err:
            try:
                done = subprocess.run(command, input=prompt.encode("utf-8"), stdout=out, stderr=err,
                                      cwd=str(workspace), timeout=timeout, check=False)
                code = done.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
    except OSError as exc:
        return PeerResult(False, "", session, f"could not start {command[0]}: {exc}")
    output = out_path.read_text(encoding="utf-8", errors="replace")
    parsed = _parse_codex(output, last) if peer == "codex" else _parse_claude(output)
    kept = parsed.session or session
    if timed_out:
        return PeerResult(False, "", kept, f"the peer timed out after {timeout}s; its work may be partly done")
    if code != 0:
        tail = err_path.read_text(encoding="utf-8", errors="replace")[-ERROR_TAIL_CHARS:].strip()
        return PeerResult(False, "", kept, f"the peer exited with code {code}: {tail or parsed.error}")
    if parsed.ok and not kept:
        return PeerResult(False, "", None, "the peer replied without a session id, so the conversation "
                                           "could not be continued; its reply was discarded")
    return PeerResult(parsed.ok, parsed.reply, kept, parsed.error)
