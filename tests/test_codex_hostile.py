"""Additional adversarial cases at e0b44d5; all peers and state are disposable.

Regression cases assert the documented contract. Interleaving hooks pause at real command boundaries without
timing-dependent sleeps; subprocess cases additionally exercise actual flock.
"""
from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from duetlib import base, cli, gates, peers, state as st  # noqa: E402


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Even parser-only cases cannot accidentally use the real run store/CLIs."""
    for name in tuple(os.environ):
        if name.startswith("FAKE_") or name in (
            "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "DUET_KEEP_ENV",
            "DUET_SNAPSHOT_MAX_MB",
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("DUET_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("DUET_CODEX_BIN", str(ROOT / "tests" / "fake_codex.py"))
    monkeypatch.setenv("DUET_CLAUDE_BIN", str(ROOT / "tests" / "fake_claude.py"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print('original')\n", encoding="utf-8")
    return {"tmp": tmp_path, "ws": ws}


def command(run, verb, *args):
    return cli.main([verb, "--run", str(run), *args])


def start(env, capsys, *args, proposals=False):
    goal = env["tmp"] / "goal.md"
    goal.write_text("Build a tested greeter.", encoding="utf-8")
    skip = [] if proposals else ["--skip-proposals", "small fixture"]
    acceptance = list(args) if args else ["--accept", "true"]
    assert cli.main(["init", "--workspace", str(env["ws"]), "--goal-file", str(goal),
                     *skip, *acceptance]) == 0
    run = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert env["tmp"] in run.parents
    return run


def say(run, phase="review", verdict=None, *extra):
    draft = run / "drafts" / (threading.current_thread().name + ".md")
    draft.write_text("Host observation.", encoding="utf-8")
    flags = ["--verdict", verdict] if verdict else []
    return command(run, "say", "--phase", phase, "--file", str(draft), *flags, *extra)


def peer(run, phase="review", *extra):
    return command(run, "peer", "--phase", phase, *extra)


def ready(env, capsys, *args, proposals=False):
    run = start(env, capsys, *args, proposals=proposals)
    if proposals:
        assert say(run, "propose") == 0
        assert peer(run, "propose") == 0
        assert say(run, "plan", "approve") == 0
        assert peer(run, "plan") == 0
    assert say(run, "build") == 0
    (env["ws"] / "app.py").write_text("print('host implementation')\n", encoding="utf-8")
    assert command(run, "check") == 0
    assert peer(run) == 0
    assert cli._report(run)["ready_to_finish"]
    return run


def stub(env, monkeypatch, body, which="codex"):
    """Executable Python stand-in, never a wrapper around an installed client."""
    script = env["tmp"] / (which + "-stub")
    script.write_text("#!" + sys.executable + "\nimport json, os, sys, time\n"
                      "from pathlib import Path\nsys.stdin.read()\n" + body, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("DUET_" + which.upper() + "_BIN", str(script))


def events(*items):
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n"


THREAD = {"type": "thread.started", "thread_id": "a-valid-thread-123"}
COMPLETE = {"type": "turn.completed", "usage": {"input_tokens": 111, "output_tokens": 13}}


def message(text):
    return {"type": "item.completed", "item": {"id": "msg", "type": "agent_message", "text": text}}


# Codex JSONL: tool output/reasoning are not messages; a message may itself be JSON.
@pytest.mark.parametrize("reply", [
    "Done.\nVERDICT: REVISE",
    '{"type":"turn.failed","error":{"message":"a quoted example"}}',
    "```json\n{\"verdict\": \"approve\"}\n```\nVERDICT: REVISE",
])
def test_stream_uses_last_agent_message_not_reasoning_or_tool_output(tmp_path, reply):
    stream = events(
        THREAD,
        {"type": "item.completed", "item": {"type": "reasoning", "text": "VERDICT: APPROVE"}},
        message("An early progress update.\nVERDICT: APPROVE"),
        {"type": "item.completed", "item": {"type": "command_execution", "command": "pytest",
         "aggregated_output": "VERDICT: APPROVE", "exit_code": 1, "status": "failed"}},
        message(reply), COMPLETE,
    )
    result = peers._parse_codex(stream, tmp_path / "absent-last.txt")
    assert result.ok and result.reply == reply
    assert result.usage == {"input_tokens": 111, "output_tokens": 13}
    assert gates.parse_verdict(result.reply) != "approve"


def test_reasoning_and_commands_without_agent_message_fail(tmp_path):
    result = peers._parse_codex(events(THREAD,
        {"type": "item.completed", "item": {"type": "reasoning", "text": "VERDICT: APPROVE"}},
        {"type": "item.completed", "item": {"type": "command_execution",
         "aggregated_output": "VERDICT: APPROVE", "status": "completed"}}, COMPLETE), tmp_path / "last")
    assert not result.ok and not result.reply


def test_empty_final_agent_message_cannot_reuse_earlier_approval(tmp_path):
    result = peers._parse_codex(events(THREAD, message("VERDICT: APPROVE"), message("  "), COMPLETE),
                                tmp_path / "last")
    assert not result.ok


def test_recoverable_stream_error_followed_by_completed_turn_is_usable(tmp_path):
    result = peers._parse_codex(events(THREAD,
        {"type": "error", "message": "Reconnecting... 1/5"},
        message("VERDICT: REVISE"), COMPLETE), tmp_path / "last")
    assert result.ok and result.reply == "VERDICT: REVISE"


@pytest.mark.parametrize("ending", [
    {"type": "turn.failed", "error": {"message": "stream disconnected before completion"}},
    None,
], ids=["turn-failed", "truncated-after-progress"])
def test_incomplete_codex_stream_cannot_finish_run(env, capsys, monkeypatch, ending):
    run = ready(env, capsys)
    assert say(run, "review", "revise") == 0
    # Require the peer's new verdict, instead of relying on its earlier approval.
    (env["ws"] / "app.py").write_text("print('new host edit')\n", encoding="utf-8")
    assert command(run, "check") == 0
    rows = [THREAD, message("Preliminary result.\nVERDICT: APPROVE")]
    if ending:
        rows.append(ending)
    stub(env, monkeypatch, "print(" + repr(events(*rows)) + ", end='')\n")
    code = peer(run)
    finished = command(run, "finish")
    assert code == cli.EXIT_PEER_FAILED and finished == cli.EXIT_NOT_DONE


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_raw_unicode_jsonl_fallback_preserves_final_message(tmp_path, separator):
    reply = "Reviewed café" + separator + "details.\nVERDICT: REVISE"
    result = peers._parse_codex(events(THREAD, message(reply), COMPLETE), tmp_path / "missing-last")
    assert result.ok and result.reply == reply


# Crash consistency across the message journal and the derived state file.
def test_crash_after_failed_check_message_invalidates_old_pass(env, capsys, monkeypatch):
    flag = env["tmp"] / "fail-now"
    run = ready(env, capsys, "--accept", "test ! -e " + shlex.quote(str(flag)))
    flag.touch()
    real = st._write_state

    def crash_before_state(run_dir, state):
        if state.get("last_check") and not state["last_check"]["passed"]:
            raise OSError("simulated power loss before state write")
        return real(run_dir, state)

    with monkeypatch.context() as patch:
        patch.setattr(st, "_write_state", crash_before_state)
        assert command(run, "check") != 0
    assert st.read_messages(run)[-1]["kind"] == "check"
    assert st.read_messages(run)[-1]["meta"]["passed"] is False
    assert command(run, "finish") == cli.EXIT_NOT_DONE


def test_crash_after_replan_message_cannot_finish_previous_plan(env, capsys, monkeypatch):
    run = ready(env, capsys, proposals=True)
    real = st._write_state

    def crash_before_phase(run_dir, state):
        if state.get("phase") == "plan":
            raise OSError("simulated crash after journal append")
        return real(run_dir, state)

    with monkeypatch.context() as patch:
        patch.setattr(st, "_write_state", crash_before_phase)
        assert say(run, "plan", "revise", "--replan", "requirements changed") != 0
    assert any(m["kind"] == "replan" for m in st.read_messages(run))
    assert command(run, "finish") == cli.EXIT_NOT_DONE


def test_peer_receipt_state_write_failure_counts_once_and_can_continue(env, capsys, monkeypatch):
    run = start(env, capsys)
    assert say(run, "build") == 0
    (env["ws"] / "app.py").write_text("print('host edit')\n", encoding="utf-8")
    assert command(run, "check") == 0
    real = st._write_state
    crashed = []

    def fail_receipt_once(run_dir, state):
        if state.get("peer_primed") and not state.get("pending_turn") and not crashed:
            crashed.append(True)
            raise RuntimeError("simulated loss after peer message")
        return real(run_dir, state)

    with monkeypatch.context() as patch:
        patch.setattr(st, "_write_state", fail_receipt_once)
        assert peer(run) == cli.EXIT_INTERNAL
    assert st.load_state(run)["peer_turns"] == 1
    assert not st.load_state(run)["pending_turn"]
    assert peer(run) == 0
    assert st.load_state(run)["peer_turns"] == 2
    assert command(run, "finish") == 0


def test_crashed_receipt_cost_still_counts_against_cap(env, capsys, monkeypatch):
    run = start(env, capsys, "--accept", "true", "--peer", "claude", "--max-cost", "0.25")
    real = st._write_state
    crashed = []

    def fail_cost_once(run_dir, state):
        if state.get("peer_usage", {}).get("cost_usd") == 0.25 and not crashed:
            crashed.append(True)
            raise OSError("simulated loss after cost receipt")
        return real(run_dir, state)

    with monkeypatch.context() as patch:
        patch.setattr(st, "_write_state", fail_cost_once)
        assert peer(run, "frame") != 0
    assert any(m["meta"].get("usage", {}).get("cost_usd") == 0.25 for m in st.read_messages(run))
    assert peer(run, "frame") == cli.EXIT_CAP


def test_atomic_state_replace_failure_keeps_old_state_and_run_usable(env, capsys, monkeypatch):
    run = start(env, capsys)
    before = st.load_state(run)
    real = st.os.replace

    def fail_replace(source, dest):
        if Path(dest) == run / st.STATE_FILE:
            raise OSError("rename failed before publication")
        return real(source, dest)

    with monkeypatch.context() as patch:
        patch.setattr(st.os, "replace", fail_replace)
        assert command(run, "extend", "--turns", "1", "--reason", "one more") != 0
    assert st.load_state(run) == before
    assert command(run, "extend", "--turns", "1", "--reason", "retry") == 0
    assert st.load_state(run)["max_turns"] == before["max_turns"] + 1


# Real races, and deterministic interleavings at the same public-command boundaries.
@pytest.mark.parametrize("invalidate", ["withdraw-review", "replan", "failing-check"])
def test_finish_revalidates_before_committing_done(env, capsys, monkeypatch, invalidate):
    flag = env["tmp"] / "fail-now"
    run = ready(env, capsys, "--accept", "test ! -e " + shlex.quote(str(flag)), proposals=True)
    original = cli._report
    completed = threading.Event()
    tasks = []
    if invalidate == "withdraw-review":
        monkeypatch.setenv("FAKE_REPLY", "I found a bug.\nVERDICT: REVISE")

    def other_command():
        try:
            if invalidate == "withdraw-review":
                return peer(run)
            if invalidate == "replan":
                return say(run, "plan", "revise", "--replan", "API changed")
            flag.touch()
            return command(run, "check")
        finally:
            completed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        def report_then_other_command(run_dir):
            report = original(run_dir)
            if not tasks:
                tasks.append(pool.submit(other_command))
                # A transactional fix may hold the state lock here. Let it
                # serialize, rather than deadlocking this regression test.
                completed.wait(timeout=1)
            return report

        with monkeypatch.context() as patch:
            patch.setattr(cli, "_report", report_then_other_command)
            result = command(run, "finish")
        mutation = tasks[0].result(timeout=10)
    state = st.load_state(run)
    assert ((result == cli.EXIT_NOT_DONE and state["status"] == "active") or
            (result == 0 and state["status"] == "done" and mutation == cli.EXIT_USAGE))


def test_two_finishes_preserve_valid_terminal_state(env, capsys):
    run = ready(env, capsys)
    barrier = threading.Barrier(2)

    def finish_together(_):
        barrier.wait(timeout=10)
        return command(run, "finish")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish_together, range(2)))
    assert results == [0, 0]
    assert st.load_state(run)["status"] == "done"
    assert cli._report(run)["check_valid_on_current_files"]
    messages = st.read_messages(run)
    assert [m["seq"] for m in messages] == list(range(1, len(messages) + 1))


def test_replan_during_peer_review_survives_late_reply(env, capsys, monkeypatch):
    run = ready(env, capsys, proposals=True)
    original = peers.run_turn

    def peer_with_interleaved_replan(*args, **kwargs):
        assert say(run, "plan", "revise", "--replan", "new requirement during review") == 0
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(peers, "run_turn", peer_with_interleaved_replan)
        assert peer(run) == 0
    assert command(run, "finish") == cli.EXIT_NOT_DONE


def test_check_during_peer_write_does_not_invent_a_host_writer(env, capsys, monkeypatch):
    run = start(env, capsys)
    assert say(run, "build") == 0

    def peer_with_interleaved_check(*args, **kwargs):
        (env["ws"] / "feature.py").write_text("peer implementation\n", encoding="utf-8")
        assert command(run, "check") == 0
        return peers.PeerResult(True, "Built it.", "peer-session")

    with monkeypatch.context() as patch:
        patch.setattr(peers, "run_turn", peer_with_interleaved_check)
        assert peer(run, "build", "--write") == 0
    assert say(run, "review", "approve") == 0
    assert cli._report(run)["writers"] == ["codex"]
    assert command(run, "finish") == 0


def test_user_message_during_check_is_preserved_and_replayed(env, capsys, monkeypatch):
    run = start(env, capsys)
    original = gates.run_check

    def checking(state, timeout, **kwargs):
        assert say(run, "frame", None, "--speaker", "user") == 0
        return original(state, timeout, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(gates, "run_check", checking)
        assert command(run, "check") == 0
    assert peer(run, "frame") == 0
    call = json.loads((env["tmp"] / "calls.jsonl").read_text().splitlines()[-1])
    assert "Host observation." in call["stdin"] and "Acceptance `true` PASSED" in call["stdin"]
    assert st.load_state(run)["phase"] == "frame"


def test_simultaneous_cost_and_turn_extensions_merge_under_lock(env, capsys):
    run = start(env, capsys, "--accept", "true", "--max-cost", "1")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: command(run, "extend", "--cost", "0.25", "--turns", "1",
                                                  "--reason", "approved extra budget"), range(8)))
    assert results == [0] * 8
    state = st.load_state(run)
    assert state["max_cost_usd"] == 3 and state["max_turns"] == 38
    messages = st.read_messages(run)
    assert [m["seq"] for m in messages] == list(range(1, len(messages) + 1))


def test_peer_lock_alias_is_same_lock_but_separate_run_is_independent(env, capsys):
    run = start(env, capsys)
    other = start(env, capsys)
    alias = env["tmp"] / "run-alias"
    alias.symlink_to(run, target_is_directory=True)
    with base.peer_lock(run) as acquired:
        assert acquired
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "duet.py"), "peer",
                               "--run", str(alias), "--phase", "frame"],
                              capture_output=True, text=True, timeout=10)
        assert proc.returncode == cli.EXIT_USAGE and "already running" in proc.stderr
        assert peer(other, "frame") == 0
    assert peer(run, "frame") == 0


# Acceptance/plan/budget transitions that are easily confused with a successful run.
def test_accept_later_and_skip_proposals_still_require_real_acceptance(env, capsys):
    run = start(env, capsys, "--accept-later")
    for phase in ("plan", "build", "propose"):
        assert say(run, phase) == cli.EXIT_ORDER
        assert peer(run, phase) == cli.EXIT_ORDER
    assert command(run, "accept", "--waive", "unexecutable prose goal") == 0
    assert say(run, "build") == 0
    assert say(run, "review", "approve") == 0
    assert peer(run) == 0
    assert command(run, "finish") == 0


def test_replan_needs_new_approvals_not_prior_user_arbitration(env, capsys):
    run = ready(env, capsys, proposals=True)
    assert say(run, "review", None, "--speaker", "user") == 0
    assert say(run, "plan", "revise", "--replan", "new design") == 0
    assert peer(run, "plan") == 0
    assert say(run, "build") == cli.EXIT_ORDER
    assert say(run, "plan", "approve") == 0
    assert say(run, "plan") == 0  # a later verdictless message withdraws plan approval
    assert peer(run, "build", "--write") == cli.EXIT_ORDER
    assert say(run, "plan", "approve") == 0
    assert say(run, "build") == 0


def test_cost_extension_cannot_overflow_to_an_unlimited_budget(env, capsys):
    run = start(env, capsys, "--accept", "true", "--max-cost", "1e308")
    command(run, "extend", "--cost", "1e308", "--reason", "finite additional cap")
    assert math.isfinite(st.load_state(run)["max_cost_usd"])


def test_failed_peer_reported_spend_still_enforces_cost_cap(env, capsys, monkeypatch):
    run = start(env, capsys, "--accept", "true", "--peer", "claude", "--max-cost", "0.2")
    data = {"type": "result", "subtype": "error_max_turns", "is_error": True,
            "result": "Stopped at max turns", "session_id": "claude-session", "total_cost_usd": 0.3,
            "usage": {"input_tokens": 100, "output_tokens": 50}}
    stub(env, monkeypatch, "print(" + repr(json.dumps(data)) + ")\n", which="claude")
    assert peer(run, "frame") == cli.EXIT_PEER_FAILED
    assert peer(run, "frame") == cli.EXIT_CAP


# Restore boundaries not exercised by ordinary file/dir/symlink swaps.
def test_restore_never_moves_external_file_through_ignored_parent_symlink(env, capsys, monkeypatch):
    ws = env["ws"]
    (ws / "pkg").mkdir()
    (ws / "pkg" / "file.py").write_text("original\n", encoding="utf-8")
    (ws / ".gitignore").write_text("pkg\n", encoding="utf-8")
    for argv in (["init", "-q"], ["add", ".gitignore"], ["add", "-f", "pkg/file.py"]):
        subprocess.run(["git", *argv], cwd=str(ws), check=True, capture_output=True)
    outside = env["tmp"] / "outside"
    outside.mkdir()
    victim = outside / "file.py"
    victim.write_text("precious external data\n", encoding="utf-8")
    run = start(env, capsys)
    stub(env, monkeypatch,
         "Path('pkg/file.py').unlink()\nPath('pkg').rmdir()\n"
         "Path('pkg').symlink_to(" + repr(str(outside)) + ", target_is_directory=True)\n"
         "print(" + repr(events(THREAD, message("VERDICT: APPROVE"), COMPLETE)) + ", end='')\n")
    assert peer(run, "frame") == cli.EXIT_READONLY_BROKEN
    assert victim.exists() and victim.read_text(encoding="utf-8") == "precious external data\n"


@pytest.mark.parametrize("populated", [False, True], ids=["empty-directory", "source-directory"])
def test_directory_mode_change_invalidates_approval(env, capsys, populated):
    folder = env["ws"] / "private"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    if populated:
        (folder / "source.py").write_text("important = True\n", encoding="utf-8")
    run = ready(env, capsys)
    folder.chmod(0o700)
    assert command(run, "finish") == cli.EXIT_NOT_DONE


def test_deep_nested_repositories_cannot_hide_changed_source(env, capsys):
    folder = env["ws"]
    for _ in range(9):
        folder = folder / "vendor"
        folder.mkdir()
        subprocess.run(["git", "init", "-q", str(folder)], check=True, capture_output=True)
    source = folder / "core.py"
    source.write_text("valid = True\n", encoding="utf-8")
    run = ready(env, capsys)
    source.write_text("valid = False\n", encoding="utf-8")
    assert command(run, "finish") == cli.EXIT_NOT_DONE


@pytest.mark.parametrize("lost_manifest", ["missing", "truncated"])
def test_lost_manifest_during_interruption_keeps_original_snapshot(env, capsys, monkeypatch, lost_manifest):
    run = start(env, capsys)
    original = (env["ws"] / "app.py").read_bytes()

    def fail_after_edit(peer_name, prompt, workspace, turn_dir, *args):
        (workspace / "app.py").write_text("unreviewed peer edit\n", encoding="utf-8")
        if lost_manifest == "missing":
            (turn_dir / "before.json").unlink()
        else:
            (turn_dir / "before.json").write_text("{", encoding="utf-8")
        raise ValueError("crash while manifest unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(peers, "run_turn", fail_after_edit)
        assert peer(run, "frame") == cli.EXIT_INTERNAL
    alternatives = [env["ws"] / "app.py", run / "turns" / "001" / "snapshot-kept" / "app.py"]
    assert any(path.is_file() and path.read_bytes() == original for path in alternatives)


def test_restore_hundreds_of_files_keeps_deleted_modified_and_new_versions(env, capsys, monkeypatch):
    ws = env["ws"]
    for i in range(256):
        path = ws / ("pkg" + str(i % 8)) / (str(i) + ".py")
        path.parent.mkdir(exist_ok=True)
        path.write_text("original " + str(i), encoding="utf-8")
    before = gates.manifest(ws)
    run = start(env, capsys)
    stub(env, monkeypatch,
         "for i in range(256):\n"
         "    p = Path('pkg' + str(i % 8)) / (str(i) + '.py')\n"
         "    if i % 2: p.unlink()\n"
         "    else: p.write_text('modified ' + str(i))\n"
         "Path('new.py').write_text('new source')\n"
         "print(" + repr(events(THREAD, message("VERDICT: APPROVE"), COMPLETE)) + ", end='')\n")
    assert peer(run, "frame") == cli.EXIT_READONLY_BROKEN
    assert gates.manifest(ws) == before
    evidence = run / "turns" / "001" / "readonly-violation" / "files"
    assert (evidence / "pkg0" / "0.py").read_text() == "modified 0"
    assert (evidence / "new.py").read_text() == "new source"


def test_sparse_large_file_restores_contents_size_and_nanosecond_mtime(env, capsys, monkeypatch):
    path = env["ws"] / "large.bin"
    with path.open("wb") as handle:
        handle.write(b"original")
        handle.truncate(100 * 1024 * 1024 + 1)
    os.utime(str(path), ns=(1_600_000_000_123456789, 1_600_000_000_123456789))
    before = path.stat()
    fingerprint = gates.fingerprint(env["ws"])
    run = start(env, capsys)
    stub(env, monkeypatch,
         "with open('large.bin', 'r+b') as f: f.write(b'modified')\n"
         "os.utime('large.bin', ns=(1700000000000000000, 1700000000000000000))\n"
         "print(" + repr(events(THREAD, message("VERDICT: APPROVE"), COMPLETE)) + ", end='')\n")
    assert peer(run, "frame") == cli.EXIT_READONLY_BROKEN
    with path.open("rb") as handle:
        assert handle.read(8) == b"original"
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert gates.fingerprint(env["ws"]) == fingerprint


def test_reconciliation_of_interrupted_writer_is_idempotent(env, capsys, monkeypatch):
    run = start(env, capsys)
    assert say(run, "build") == 0

    def fail_after_write(*args, **kwargs):
        (env["ws"] / "feature.py").write_text("peer work\n", encoding="utf-8")
        raise ValueError("runner failed")

    with monkeypatch.context() as patch:
        patch.setattr(peers, "run_turn", fail_after_write)
        assert peer(run, "build", "--write") == cli.EXIT_INTERNAL
    for _ in range(3):
        assert command(run, "check") == 0
    state = st.load_state(run)
    assert state["peer_turns"] == 1 and state["writers"] == ["codex"]
    assert len([m for m in st.read_messages(run) if m["kind"] == "peer_error"]) == 1
    assert say(run, "review", "approve") == 0
    assert command(run, "finish") == 0
