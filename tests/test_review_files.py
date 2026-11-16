"""Final-file and input-validation regressions found during the purpose review."""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from duetlib import cli, gates, peers, store


def test_large_file_change_with_identical_metadata_changes_fingerprint(tmp_path):
    source = tmp_path / "large.bin"
    with source.open("wb") as handle:
        handle.truncate(100 * 1024 * 1024 + 1)
    before = gates.fingerprint(tmp_path)
    info = source.stat()
    with source.open("r+b") as handle:
        handle.seek(100 * 1024 * 1024)
        handle.write(b"x")
    os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert source.stat().st_size == info.st_size
    assert source.stat().st_mtime_ns == info.st_mtime_ns
    assert gates.fingerprint(tmp_path) != before


@pytest.mark.parametrize("flag", ["--accept", "--waive-accept", "--skip-proposals"])
def test_init_refuses_blank_evidence_or_skip_reason(tmp_path, monkeypatch, flag):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    goal = tmp_path / "goal.md"
    goal.write_text("Deliver the required behavior")
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DUET_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setattr(peers, "launch_problem", lambda _peer: None)
    args = ["init", "--workspace", str(workspace), "--goal-file", str(goal), flag, " \t\n"]
    if flag == "--skip-proposals":
        args += ["--accept", "true"]
    assert cli.main(args) == cli.EXIT_USAGE
    assert not store.runs_home().exists()


@pytest.mark.parametrize("command", ["peer", "check", "experiment"])
@pytest.mark.parametrize("timeout", ["0", "-1"])
def test_nonpositive_timeout_is_refused_before_a_run_is_touched(command, timeout, tmp_path, capsys):
    run = tmp_path / "nonexistent"
    args = [command, "--run", str(run), "--timeout", timeout]
    if command == "peer":
        args += ["--phase", "frame"]
    elif command == "experiment":
        args += ["--cmd", "true"]
    assert cli.main(args) == cli.EXIT_USAGE
    assert "timeout must be an integer greater than 0" in capsys.readouterr().err
    assert not run.exists()


def test_successful_command_cannot_leave_background_writers(tmp_path):
    code, _output = gates.run_command("(sleep 0.3; printf leaked > leaked.txt) &", tmp_path, 5)
    assert code == 0
    time.sleep(0.5)  # past the writer's deadline; it must have been stopped with the shell
    assert not (tmp_path / "leaked.txt").exists()
