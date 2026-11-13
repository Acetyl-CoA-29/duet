"""Behaviour tests for the duet runner. Peers are local stand-ins; no model is called."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from duetlib import cli, gates, state as st  # noqa: E402

FAKE_CODEX = str(ROOT / "tests" / "fake_codex.py")
FAKE_CLAUDE = str(ROOT / "tests" / "fake_claude.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print('hi')\n")
    log = tmp_path / "peer.log"
    monkeypatch.setenv("DUET_CODEX_BIN", FAKE_CODEX)
    monkeypatch.setenv("DUET_CLAUDE_BIN", FAKE_CLAUDE)
    monkeypatch.setenv("FAKE_LOG", str(log))
    return {"ws": ws, "log": log, "tmp": tmp_path}


def write(tmp: Path, name: str, text: str) -> str:
    path = tmp / name
    path.write_text(text)
    return str(path)


def start(env, capsys, *extra) -> Path:
    goal = write(env["tmp"], "goal.md", "Build a tiny greeter.")
    code = cli.main(["init", "--workspace", str(env["ws"]), "--goal-file", goal, *extra])
    assert code == 0
    return Path(capsys.readouterr().out.strip().splitlines()[-1])


def say(env, run, text, *extra) -> int:
    return cli.main(["say", "--run", str(run), "--file", write(env["tmp"], "msg.md", text), *extra])


def calls(env):
    if not env["log"].exists():
        return []
    return [json.loads(line) for line in env["log"].read_text().splitlines()]


def test_init_creates_run_inside_workspace(env, capsys):
    run = start(env, capsys, "--accept", "true")
    assert run.parent == env["ws"] / ".duet"
    state = st.load_state(run)
    assert state["peer"] == "codex" and state["host"] == "claude"
    assert state["accept_cmd"] == "true"
    assert "Build a tiny greeter." in (run / "conversation.md").read_text()


def test_init_requires_acceptance_or_explicit_waiver(env, capsys):
    goal = write(env["tmp"], "goal.md", "x")
    assert cli.main(["init", "--workspace", str(env["ws"]), "--goal-file", goal]) == cli.EXIT_USAGE
    assert cli.main(["init", "--workspace", str(env["ws"]), "--goal-file", goal,
                     "--waive-accept", "prose task, nothing to execute"]) == 0


def test_say_appends_in_order(env, capsys):
    run = start(env, capsys, "--accept", "true")
    assert say(env, run, "first", "--phase", "frame") == 0
    assert say(env, run, "second", "--phase", "frame", "--speaker", "user") == 0
    msgs = st.read_messages(run)
    assert [m["seq"] for m in msgs] == list(range(1, len(msgs) + 1))
    assert msgs[-1]["speaker"] == "user" and msgs[-2]["speaker"] == "claude"
    assert "second" in (run / "conversation.md").read_text()


def test_first_peer_turn_starts_session_with_preamble(env, capsys):
    run = start(env, capsys, "--accept", "true")
    say(env, run, "Opening thought from the host.", "--phase", "frame")
    capsys.readouterr()
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == 0
    assert "Agreed." in capsys.readouterr().out
    call = calls(env)[0]
    assert "resume" not in call["argv"]
    assert "Build a tiny greeter." in call["stdin"]
    assert "Opening thought from the host." in call["stdin"]
    assert "VERDICT:" in call["stdin"]
    assert Path(call["cwd"]).resolve() == env["ws"].resolve()
    assert st.load_state(run)["peer_session"] == "sess-123"
    assert st.read_messages(run)[-1]["speaker"] == "codex"


def test_later_turns_resume_and_send_only_new_messages(env, capsys):
    run = start(env, capsys, "--accept", "true")
    say(env, run, "OLD-MESSAGE", "--phase", "frame")
    cli.main(["peer", "--run", str(run), "--phase", "frame"])
    say(env, run, "NEW-MESSAGE", "--phase", "debate")
    cli.main(["peer", "--run", str(run), "--phase", "debate"])
    second = calls(env)[1]
    assert "resume" in second["argv"] and "sess-123" in second["argv"]
    assert "NEW-MESSAGE" in second["stdin"]
    assert "OLD-MESSAGE" not in second["stdin"]
    assert "Build a tiny greeter." not in second["stdin"]


def test_message_text_never_reaches_argv(env, capsys):
    run = start(env, capsys, "--accept", "true")
    hostile = "it's $(rm -rf /) `whoami`; \"quoted\""
    say(env, run, hostile, "--phase", "frame")
    cli.main(["peer", "--run", str(run), "--phase", "frame"])
    call = calls(env)[0]
    assert hostile in call["stdin"]
    assert not any("rm -rf" in arg for arg in call["argv"])


def test_propose_requires_host_proposal_first(env, capsys):
    run = start(env, capsys, "--accept", "true")
    assert cli.main(["peer", "--run", str(run), "--phase", "propose"]) == cli.EXIT_BLIND
    assert calls(env) == []


def test_propose_is_blind_then_revealed_in_debate(env, capsys):
    run = start(env, capsys, "--accept", "true")
    say(env, run, "HOST-SECRET-PROPOSAL", "--phase", "propose")
    assert cli.main(["peer", "--run", str(run), "--phase", "propose"]) == 0
    assert "HOST-SECRET-PROPOSAL" not in calls(env)[0]["stdin"]
    assert cli.main(["peer", "--run", str(run), "--phase", "debate"]) == 0
    assert "HOST-SECRET-PROPOSAL" in calls(env)[1]["stdin"]


def test_sandbox_is_read_only_unless_write_requested(env, capsys):
    run = start(env, capsys, "--accept", "true")
    say(env, run, "go", "--phase", "frame")
    cli.main(["peer", "--run", str(run), "--phase", "frame"])
    cli.main(["peer", "--run", str(run), "--phase", "build", "--write"])
    first, second = calls(env)
    assert 'sandbox_mode="read-only"' in first["argv"]
    assert 'sandbox_mode="workspace-write"' in second["argv"]


@pytest.mark.parametrize("text,expected", [
    ("fine\nVERDICT: APPROVE", "approve"),
    ("VERDICT: APPROVE\nwait, no.\nverdict: revise", "revise"),
    ("I would say VERDICT: APPROVE in the middle of a line", None),
    ("no verdict here", None),
    ("**VERDICT: APPROVE**", "approve"),
])
def test_parse_verdict(text, expected):
    assert gates.parse_verdict(text) == expected


def test_check_records_result(env, capsys):
    run = start(env, capsys, "--accept", "test -f done.txt")
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_CHECK_FAILED
    (env["ws"] / "done.txt").write_text("x")
    assert cli.main(["check", "--run", str(run)]) == 0
    last = st.read_messages(run)[-1]
    assert last["speaker"] == "system" and last["meta"]["passed"] is True


def approve_both(env, run):
    say(env, run, "I reviewed it.", "--phase", "review", "--verdict", "approve")
    return cli.main(["peer", "--run", str(run), "--phase", "review"])


def test_finish_needs_passing_check(env, capsys):
    run = start(env, capsys, "--accept", "test -f done.txt")
    approve_both(env, run)
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert "acceptance" in capsys.readouterr().out.lower()


def test_finish_needs_both_approvals(env, capsys):
    run = start(env, capsys, "--accept", "true")
    cli.main(["check", "--run", str(run)])
    say(env, run, "ok", "--phase", "review", "--verdict", "approve")
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert "codex" in capsys.readouterr().out.lower()


def test_finish_succeeds_when_everything_holds(env, capsys):
    run = start(env, capsys, "--accept", "true")
    cli.main(["check", "--run", str(run)])
    assert approve_both(env, run) == 0
    assert cli.main(["finish", "--run", str(run)]) == 0
    assert st.load_state(run)["status"] == "done"


def test_finish_rejects_changes_made_after_check_and_approval(env, capsys):
    run = start(env, capsys, "--accept", "true")
    cli.main(["check", "--run", str(run)])
    approve_both(env, run)
    (env["ws"] / "app.py").write_text("print('changed')\n")
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert "changed" in capsys.readouterr().out.lower()


def test_peer_cannot_approve_its_own_write_turn(env, capsys, monkeypatch):
    run = start(env, capsys, "--accept", "true")
    say(env, run, "please build", "--phase", "build", "--verdict", "approve")
    cli.main(["peer", "--run", str(run), "--phase", "build", "--write"])
    last = st.read_messages(run)[-1]
    assert last["meta"]["verdict"] is None and last["meta"]["verdict_ignored"] == "approve"
    cli.main(["check", "--run", str(run)])
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE


def test_waived_acceptance_still_needs_both_approvals(env, capsys):
    run = start(env, capsys, "--waive-accept", "prose only")
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert approve_both(env, run) == 0
    assert cli.main(["finish", "--run", str(run)]) == 0


def test_turn_cap_blocks_until_extended(env, capsys):
    run = start(env, capsys, "--accept", "true", "--max-turns", "1")
    say(env, run, "a", "--phase", "frame")
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == 0
    assert cli.main(["peer", "--run", str(run), "--phase", "debate"]) == cli.EXIT_CAP
    assert len(calls(env)) == 1
    assert cli.main(["extend", "--run", str(run), "--turns", "2", "--reason", "user said continue"]) == 0
    assert cli.main(["peer", "--run", str(run), "--phase", "debate"]) == 0


def test_repeated_identical_failure_stalls_until_user_speaks(env, capsys):
    run = start(env, capsys, "--accept", "echo same failure; exit 1")
    for _ in range(3):
        assert cli.main(["check", "--run", str(run)]) == cli.EXIT_CHECK_FAILED
    assert cli.main(["peer", "--run", str(run), "--phase", "build"]) == cli.EXIT_STALLED
    assert calls(env) == []
    say(env, run, "Try the other approach.", "--speaker", "user", "--phase", "build")
    assert cli.main(["peer", "--run", str(run), "--phase", "build"]) == 0


def test_peer_failure_is_recorded_and_session_kept(env, capsys, monkeypatch):
    run = start(env, capsys, "--accept", "true")
    say(env, run, "a", "--phase", "frame")
    monkeypatch.setenv("FAKE_EXIT", "7")
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == cli.EXIT_PEER_FAILED
    state = st.load_state(run)
    assert state["peer_session"] == "sess-123" and state["peer_turns"] == 1  # failed attempts count toward the cap
    assert st.read_messages(run)[-1]["kind"] == "peer_error"
    monkeypatch.delenv("FAKE_EXIT")
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == 0
    retry = calls(env)[-1]
    assert "resume" in retry["argv"] and "\na\n" in retry["stdin"]


def test_peer_timeout_is_reported(env, capsys, monkeypatch):
    run = start(env, capsys, "--accept", "true")
    say(env, run, "a", "--phase", "frame")
    monkeypatch.setenv("FAKE_SLEEP", "5")
    assert cli.main(["peer", "--run", str(run), "--phase", "frame", "--timeout", "1"]) == cli.EXIT_PEER_FAILED
    assert "timed out" in st.read_messages(run)[-1]["text"]
    assert st.load_state(run)["peer_session"] == "sess-123"


def test_reply_falls_back_to_event_stream(env, capsys, monkeypatch):
    run = start(env, capsys, "--accept", "true")
    say(env, run, "a", "--phase", "frame")
    monkeypatch.setenv("FAKE_NO_LAST_FILE", "1")
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == 0
    assert "Agreed." in st.read_messages(run)[-1]["text"]


def test_claude_can_be_the_peer(env, capsys):
    run = start(env, capsys, "--accept", "true", "--peer", "claude")
    assert st.load_state(run)["host"] == "codex"
    say(env, run, "hello from codex", "--phase", "frame")
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == 0
    assert cli.main(["peer", "--run", str(run), "--phase", "build", "--write"]) == 0
    first, second = calls(env)
    assert "-p" in first["argv"] and "plan" in first["argv"]
    assert "--resume" in second["argv"] and "claude-sess-9" in second["argv"]
    assert "acceptEdits" in second["argv"]
    assert st.read_messages(run)[-1]["speaker"] == "claude"


def test_finished_run_accepts_no_more_turns(env, capsys):
    run = start(env, capsys, "--accept", "true")
    cli.main(["check", "--run", str(run)])
    approve_both(env, run)
    cli.main(["finish", "--run", str(run)])
    assert cli.main(["peer", "--run", str(run), "--phase", "debate"]) == cli.EXIT_USAGE


def test_status_reports_gate_state(env, capsys):
    run = start(env, capsys, "--accept", "true")
    capsys.readouterr()
    assert cli.main(["status", "--run", str(run)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready_to_finish"] is False and report["peer_turns"] == 0
    assert report["blocking"]


def test_fingerprint_ignores_run_folder_but_sees_source_edits(env, capsys):
    run = start(env, capsys, "--accept", "true")
    before = gates.fingerprint(env["ws"])
    (run / "scratch.txt").write_text("noise")
    assert gates.fingerprint(env["ws"]) == before
    (env["ws"] / "new.py").write_text("x = 1\n")
    assert gates.fingerprint(env["ws"]) != before


def test_peer_effort_is_passed_to_codex_and_validated(env, capsys):
    goal = write(env["tmp"], "goal.md", "x")
    assert cli.main(["init", "--workspace", str(env["ws"]), "--goal-file", goal, "--accept", "true",
                     "--peer-effort", 'low" -c evil="1']) == cli.EXIT_USAGE
    run = start(env, capsys, "--accept", "true", "--peer-effort", "low")
    say(env, run, "a", "--phase", "frame")
    cli.main(["peer", "--run", str(run), "--phase", "frame"])
    cli.main(["peer", "--run", str(run), "--phase", "frame"])
    assert all('model_reasoning_effort="low"' in call["argv"] for call in calls(env))


def test_entry_script_runs_through_a_symlink(env, tmp_path):
    import subprocess
    link = tmp_path / "linked-skill"
    link.symlink_to(ROOT)
    done = subprocess.run([sys.executable, str(link / "scripts" / "duet.py"), "status", "--run", str(tmp_path)],
                          capture_output=True, text=True)
    assert done.returncode == cli.EXIT_USAGE and "not a duet run folder" in done.stderr


def test_accept_replaces_command_and_discards_old_result(env, capsys):
    run = start(env, capsys, "--accept", "false")
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_CHECK_FAILED
    assert cli.main(["accept", "--run", str(run), "--cmd", "true"]) == 0
    state = st.load_state(run)
    assert state["accept_cmd"] == "true" and state["last_check"] is None
    assert cli.main(["check", "--run", str(run)]) == 0


def test_user_reply_gives_a_fresh_failure_budget(env, capsys):
    run = start(env, capsys, "--accept", "echo same failure; exit 1")
    for _ in range(3):
        cli.main(["check", "--run", str(run)])
    say(env, run, "Keep going.", "--speaker", "user", "--phase", "build")
    cli.main(["check", "--run", str(run)])
    assert cli.main(["peer", "--run", str(run), "--phase", "build"]) == 0


def test_repeated_peer_failures_hit_the_turn_cap(env, capsys, monkeypatch):
    run = start(env, capsys, "--accept", "true", "--max-turns", "2")
    monkeypatch.setenv("FAKE_EXIT", "7")
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == cli.EXIT_PEER_FAILED
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == cli.EXIT_PEER_FAILED
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == cli.EXIT_CAP


def _git_workspace(env):
    import subprocess
    ws = env["ws"]
    (ws / "tools" / "build").mkdir(parents=True)
    (ws / "tools" / "build" / "release.sh").write_text("echo one\n")
    (ws / ".gitignore").write_text("ignored.txt\n")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x"]):
        subprocess.run(cmd, cwd=str(ws), check=True)
    return ws


def test_fingerprint_sees_tracked_files_in_build_named_folders(env):
    ws = _git_workspace(env)
    before = gates.fingerprint(ws)
    (ws / "tools" / "build" / "release.sh").write_text("echo two\n")
    assert gates.fingerprint(ws) != before


def test_fingerprint_in_git_ignores_caches_and_ignored_files(env):
    ws = _git_workspace(env)
    before = gates.fingerprint(ws)
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "x.pyc").write_text("cache")
    (ws / "ignored.txt").write_text("local only")
    assert gates.fingerprint(ws) == before


def test_fingerprint_sees_executable_bit(env):
    before = gates.fingerprint(env["ws"])
    (env["ws"] / "app.py").chmod(0o755)
    assert gates.fingerprint(env["ws"]) != before


def test_check_that_changes_files_cannot_be_finished_on(env, capsys):
    run = start(env, capsys, "--accept", "date +%s%N >> stamp.txt; echo $RANDOM >> stamp.txt")
    assert cli.main(["check", "--run", str(run)]) == 0
    assert st.load_state(run)["last_check"]["stable"] is False
    approve_both(env, run)
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert "while the acceptance command was running" in capsys.readouterr().out


def test_reply_without_session_id_is_a_failure(env, capsys, monkeypatch):
    run = start(env, capsys, "--accept", "true")
    monkeypatch.setenv("FAKE_SESSION", "")
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == cli.EXIT_PEER_FAILED
    assert "session id" in st.read_messages(run)[-1]["text"]
