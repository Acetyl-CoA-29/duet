"""Tests for the comparison harness in eval/. Stand-in clients only: no model is ever called."""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "eval"))

from evallib import arms, cli, duetinfo, proc, runner, scoring, summary, usage  # noqa: E402
from evallib import tasks as tasklib  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_eval_cli.py"
ACCEPT = "python3 -m pytest -q -p no:cacheprovider tests"
SECRET_PROMPT_MARK = "ZEBRA-GOAL-MARKER"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def task_dir(tmp_path: Path) -> Path:
    """A tiny task whose naive solution passes the visible test and fails the hidden ones."""
    root = tmp_path / "tasks" / "adder"
    write(root / "task.md", f"Implement add(a, b) in calc.py. {SECRET_PROMPT_MARK}\n")
    write(root / "task.json", json.dumps({"accept": ACCEPT, "protected": ["tests"]}))
    write(root / "starter" / "calc.py", "def add(a, b):\n    raise NotImplementedError\n")
    write(root / "starter" / "tests" / "test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    write(root / "hidden" / "test_calc_hidden.py",
          "from calc import add\n\n\ndef test_negative():\n    assert add(-1, 1) == 0\n\n\n"
          "def test_small():\n    assert add(2, 2) == 4\n")
    write(root / "solutions" / "reference" / "calc.py", "def add(a, b):\n    return a + b\n")
    write(root / "solutions" / "naive" / "calc.py", "def add(a, b):\n    return abs(a) + abs(b)\n")
    return root


@pytest.fixture()
def fake_clients(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    FAKE.chmod(FAKE.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv(arms.CLAUDE_BIN_ENV, str(FAKE))
    monkeypatch.setenv(arms.CODEX_BIN_ENV, str(FAKE))
    monkeypatch.setenv("FAKE_EVAL_LOG", str(log))
    for name in ("FAKE_EVAL_OVERLAY", "FAKE_EVAL_SLEEP", "FAKE_EVAL_EXIT", "FAKE_EVAL_DUET",
                 "FAKE_EVAL_TAMPER", "FAKE_EVAL_CHILD_PID", "FAKE_EVAL_REPLY"):
        monkeypatch.delenv(name, raising=False)
    return log


def options(tmp_path: Path, **changes: Any) -> runner.RunOptions:
    base: Dict[str, Any] = {"session_dir": tmp_path / "results" / "s1", "timeout": 60, "max_cost": 2.0,
                            "effort": "low", "skill_root": tmp_path / "skill",
                            "duet_home": tmp_path / "duet-home"}
    base.update(changes)
    return runner.RunOptions(**base)


def calls(log: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- tasks

def test_load_task_reads_goal_and_commands(task_dir: Path) -> None:
    task = tasklib.load_task(task_dir)
    assert task.name == "adder" and task.accept == ACCEPT
    assert task.hidden_cmd == tasklib.DEFAULT_HIDDEN_CMD and task.protected == ("tests",)


@pytest.mark.parametrize("missing", ["task.md", "starter", "hidden"])
def test_load_task_names_what_is_missing(task_dir: Path, missing: str) -> None:
    target = task_dir / missing
    target.unlink() if target.is_file() else __import__("shutil").rmtree(str(target))
    with pytest.raises(tasklib.TaskError, match=missing):
        tasklib.load_task(task_dir)


def test_load_task_rejects_bad_config(task_dir: Path) -> None:
    write(task_dir / "task.json", "{not json")
    with pytest.raises(tasklib.TaskError, match="not valid JSON"):
        tasklib.load_task(task_dir)
    write(task_dir / "task.json", "[1]")
    with pytest.raises(tasklib.TaskError, match="JSON object"):
        tasklib.load_task(task_dir)
    write(task_dir / "task.json", "{}")
    with pytest.raises(tasklib.TaskError, match="accept"):
        tasklib.load_task(task_dir)
    write(task_dir / "task.json", json.dumps({"accept": ACCEPT}))
    write(task_dir / "task.md", "  \n")
    with pytest.raises(tasklib.TaskError, match="empty"):
        tasklib.load_task(task_dir)


def test_accept_script_is_used_copied_and_protected(task_dir: Path, tmp_path: Path) -> None:
    (task_dir / "task.json").unlink()
    write(task_dir / "accept.sh", ACCEPT + "\n")
    task = tasklib.load_task(task_dir)
    assert task.accept == "sh accept.sh" and "accept.sh" in task.protected
    workspace = tmp_path / "ws"
    tasklib.prepare_workspace(task, workspace)
    tasklib.copy_tree(task.solution("reference"), workspace)
    write(workspace / "accept.sh", "true\n")
    score = scoring.score_workspace(task, workspace, tmp_path / "score")
    assert score.tampered == ["accept.sh"] and score.visible.passed and score.hidden.passed


def test_discover_selects_and_rejects_unknown(task_dir: Path) -> None:
    assert [t.name for t in tasklib.discover(task_dir.parent)] == ["adder"]
    assert [t.name for t in tasklib.discover(task_dir.parent, ["adder"])] == ["adder"]
    with pytest.raises(tasklib.TaskError, match="unknown task"):
        tasklib.discover(task_dir.parent, ["nope"])
    with pytest.raises(tasklib.TaskError, match="no such tasks directory"):
        tasklib.discover(task_dir.parent / "absent")


def test_workspace_holds_only_the_starter(task_dir: Path, tmp_path: Path) -> None:
    task = tasklib.load_task(task_dir)
    workspace = tmp_path / "ws"
    tasklib.prepare_workspace(task, workspace)
    names = {p.relative_to(workspace).parts[0] for p in workspace.iterdir()}
    assert names == {"calc.py", "tests", ".git", ".gitignore"}
    assert not list(workspace.rglob("*hidden*")) and not list(workspace.rglob("solutions"))
    assert ".duet/" in (workspace / ".git" / "info" / "exclude").read_text().splitlines()
    with pytest.raises(tasklib.TaskError, match="already exists"):
        tasklib.prepare_workspace(task, workspace)
    write(workspace / "calc.py", "def add(a, b):\n    return a + b\n")
    write(workspace / "new.py", "x = 1\n")
    diff = tasklib.workspace_diff(workspace)
    assert "return a + b" in diff and "new.py" in diff


def test_git_failures_do_not_stop_a_run(task_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("git is not installed")
    monkeypatch.setattr(tasklib.subprocess, "run", no_git)
    workspace = tmp_path / "ws"
    tasklib.prepare_workspace(tasklib.load_task(task_dir), workspace)
    assert (workspace / "calc.py").is_file() and tasklib.workspace_diff(workspace) == ""


# ---------------------------------------------------------------- one run

def test_reference_run_passes_and_never_sees_the_oracle(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    task = tasklib.load_task(task_dir)
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(task.solution("reference")))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-other-lab")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-deploy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-own-lab")
    opts = options(tmp_path)
    record = runner.run_one(task, "solo-claude", 1, opts)

    assert record["status"] == "ok" and record["exit_code"] == 0 and not record["timed_out"]
    assert record["visible"]["passed"] and record["hidden"]["passed"]
    assert record["hidden"]["tests_passed"] == 2 and record["hidden"]["tests_failed"] == 0
    assert record["visible_pass_hidden_fail"] is False
    assert record["cost_usd"] == pytest.approx(0.4321) and record["tokens"]["output_tokens"] == 300
    assert record["session"] == "session-fake" and record["duet"] is None and record["seconds"] >= 0

    call = calls(fake_clients)[0]
    assert SECRET_PROMPT_MARK in call["stdin"] and ACCEPT in call["stdin"]
    assert not any(SECRET_PROMPT_MARK in arg for arg in call["argv"])          # prompt went in on stdin only
    assert not any("hidden" in name or "solutions" in name for name in call["files"])
    assert "calc.py" in call["files"] and "tests/test_calc.py" in call["files"]
    assert str(task_dir) not in call["cwd"] and "adder" not in call["cwd"]      # opaque, throwaway folder
    assert Path(call["cwd"]).resolve().is_relative_to(opts.session_dir.resolve()) if hasattr(Path, "is_relative_to") \
        else str(opts.session_dir.resolve()) in str(Path(call["cwd"]).resolve())
    assert "OPENAI_API_KEY" not in call["secrets"] and "GITHUB_TOKEN" not in call["secrets"]
    assert "ANTHROPIC_API_KEY" in call["secrets"]

    saved = json.loads((opts.session_dir / "records" / "adder__solo-claude__s1.json").read_text())
    assert saved == record
    assert not Path(call["cwd"]).exists() and record["workspace"] is None        # workspace thrown away
    logs = Path(record["logs"])
    assert (logs / "prompt.md").is_file() and "return a + b" in (logs / "diff.patch").read_text()
    assert not (logs / "copy").exists()                                          # scoring copy removed too


def test_naive_run_looks_done_but_is_not(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    task = tasklib.load_task(task_dir)
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(task.solution("naive")))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-other-lab")
    record = runner.run_one(task, "solo-codex", 2, options(tmp_path, keep_workspaces=True))
    assert record["visible"]["passed"] and not record["hidden"]["passed"]
    assert record["visible_pass_hidden_fail"] is True
    assert record["hidden"]["tests_passed"] == 1 and record["hidden"]["tests_failed"] == 1
    assert record["cost_usd"] is None and "tokens" in record["cost_note"]
    assert record["tokens"] == {"input_tokens": 1500, "cached_input_tokens": 400, "output_tokens": 75}
    assert record["session"] == "thread-fake" and record["seed"] == 2
    assert Path(record["workspace"]).is_dir()
    assert not list(Path(record["workspace"]).rglob("*hidden*"))                 # oracle only ever in the copy
    assert "ANTHROPIC_API_KEY" not in calls(fake_clients)[0]["secrets"]


def test_unsolved_run_fails_both(task_dir: Path, tmp_path: Path, fake_clients: Path) -> None:
    record = runner.run_one(tasklib.load_task(task_dir), "solo-claude", 1, options(tmp_path))
    assert not record["visible"]["passed"] and not record["hidden"]["passed"]
    assert record["visible_pass_hidden_fail"] is False


def test_timeout_stops_the_whole_process_group(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_EVAL_SLEEP", "60")
    monkeypatch.setenv("FAKE_EVAL_CHILD_PID", str(pid_file))
    started = time.monotonic()
    record = runner.run_one(tasklib.load_task(task_dir), "solo-codex", 1, options(tmp_path, timeout=2))
    assert time.monotonic() - started < 30
    assert record["status"] == "timeout" and record["timed_out"] and record["exit_code"] is None
    assert "limit" in record["error"] and not record["hidden"]["passed"]
    grandchild = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(grandchild, 9)
        pytest.fail("a process started by the client outlived the timeout")


def test_failed_client_is_recorded_and_still_scored(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EVAL_EXIT", "3")
    record = runner.run_one(tasklib.load_task(task_dir), "solo-claude", 1, options(tmp_path))
    assert record["status"] == "error" and record["exit_code"] == 3 and record["usage_parsed"] is False
    assert record["cost_usd"] is None and not record["visible"]["passed"]


def test_missing_client_is_reported_not_raised(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(arms.CLAUDE_BIN_ENV, str(tmp_path / "no-such-client"))
    record = runner.run_one(tasklib.load_task(task_dir), "solo-claude", 1, options(tmp_path))
    assert record["status"] == "not_started" and "could not start" in record["error"]


def test_weakened_tests_are_restored_before_scoring(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EVAL_TAMPER", "tests/test_calc.py")
    record = runner.run_one(tasklib.load_task(task_dir), "solo-claude", 1, options(tmp_path))
    assert record["tampered"] == ["tests/test_calc.py"]
    assert not record["visible"]["passed"]                 # the real test ran, not the weakened one


def test_rerun_replaces_the_previous_logs(task_dir: Path, tmp_path: Path, fake_clients: Path) -> None:
    task, opts = tasklib.load_task(task_dir), options(tmp_path)
    first = runner.run_one(task, "solo-claude", 1, opts)
    write(Path(first["logs"]) / "stale.txt", "old")
    second = runner.run_one(task, "solo-claude", 1, opts)
    assert not (Path(second["logs"]) / "stale.txt").exists()


def test_scoring_folder_must_be_outside_the_workspace(task_dir: Path, tmp_path: Path) -> None:
    task = tasklib.load_task(task_dir)
    tasklib.prepare_workspace(task, tmp_path / "ws")
    with pytest.raises(ValueError, match="outside"):
        scoring.score_workspace(task, tmp_path / "ws", tmp_path / "ws" / "score")


def test_parse_counts() -> None:
    assert scoring.parse_counts("...\n3 passed in 0.01s") == {"passed": 3, "failed": 0}
    assert scoring.parse_counts("2 failed, 5 passed, 1 error in 1s\n") == {"passed": 5, "failed": 3}
    assert scoring.parse_counts("1 error in 0.1s") == {"passed": 0, "failed": 1}
    assert scoring.parse_counts("no summary here") == {"passed": None, "failed": None}


# ---------------------------------------------------------------- duet arms

def test_duet_arm_records_a_finished_duet(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    task = tasklib.load_task(task_dir)
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(task.solution("reference")))
    store = write(tmp_path / "state" / "runs" / "r1" / "conversation.md", "the transcript").parent
    status = {"status": "done", "peer_turns": 5, "max_cost_usd": 6, "run": str(store),
              "peer_usage": {"cost_usd": 1.25, "input_tokens": 10, "output_tokens": 5}}
    monkeypatch.setenv("FAKE_EVAL_REPLY", "Finished.\n```json\n" + json.dumps(status, indent=2) + "\n```")
    record = runner.run_one(task, "duet-codex-host", 1, options(tmp_path))
    assert record["is_duet"] and record["duet"]["finished"] is True and record["duet"]["peer_turns"] == 5
    assert record["peer_cost_usd"] == 1.25 and record["cost_usd"] == 1.25 and record["peer_tokens"] == 15
    assert (Path(record["logs"]) / "duet-run" / "conversation.md").read_text() == "the transcript"
    assert summary.arm_stats([record])["mean_tokens"] == 1500 + 75 + 15       # host plus peer
    call = calls(fake_clients)[0]
    assert "--peer claude" in call["stdin"] and "--max-cost 2" in call["stdin"] and "--max-turns 8" in call["stdin"]
    assert str(tmp_path / "skill" / "SKILL.md") in call["stdin"]
    assert "sandbox_workspace_write.network_access=true" in call["argv"] and "--add-dir" in call["argv"]
    assert str(tmp_path / "duet-home") not in call["argv"]                    # absent folder is not added


def test_duet_status_is_found_in_the_run_folder(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EVAL_DUET", json.dumps({"status": "active", "peer_turns": 2}))
    record = runner.run_one(tasklib.load_task(task_dir), "duet-claude-host", 1, options(tmp_path))
    assert record["duet"]["finished"] is False and record["duet"]["status"] == "active"
    assert record["duet"]["started"] is True
    assert record["cost_usd"] == pytest.approx(0.4321)                 # host dollars; the peer reported none
    assert (Path(record["logs"]) / "duet" / "run-1" / "state.json").is_file()
    call = calls(fake_clients)[0]
    runner_path = str(tmp_path / "skill" / "scripts" / "duet.py")
    assert f"Bash(python3 {runner_path} *)" in call["argv"] and "--timeout 60" in call["stdin"]
    assert "--peer-effort low" in call["stdin"] and "--peer claude" not in call["stdin"]


def test_duet_outcome_is_unknown_without_evidence(tmp_path: Path) -> None:
    outcome = duetinfo.duet_outcome(tmp_path, "I could not start the duet: codex was not found.")
    assert outcome["finished"] == "unknown" and outcome["started"] is False and outcome["peer_cost_usd"] is None


def test_duet_outcome_ignores_unreadable_files(tmp_path: Path) -> None:
    write(tmp_path / ".duet" / "r" / "broken.json", "{oops")
    write(tmp_path / ".duet" / "r" / "list.json", "[1, 2]")
    write(tmp_path / ".duet" / "r" / "other.json", json.dumps({"no_status": 1}))
    assert duetinfo.duet_outcome(tmp_path, "text with {braces} and {\"a\": [1]}")["finished"] == "unknown"
    write(tmp_path / ".duet" / "r" / "state.json", json.dumps({"status": "DONE", "turns": [{"cost_usd": 0.5}]}))
    outcome = duetinfo.duet_outcome(tmp_path, "")
    assert outcome["finished"] is True and outcome["peer_cost_usd"] == 0.5 and outcome["peer_turns"] is None


def test_oracle_paths_are_flagged_when_named(task_dir: Path, tmp_path: Path) -> None:
    task = tasklib.load_task(task_dir)
    clean = write(tmp_path / "out" / "stdout.txt", "ran pytest in the workspace")
    leaked = write(tmp_path / "out" / "duet" / "turn.txt", f"cat {task.hidden}/test_calc_hidden.py")
    assert runner.oracle_paths_seen(task, [clean, tmp_path / "absent"]) == []
    assert runner.oracle_paths_seen(task, [clean, leaked.parent]) == [f"turn.txt: {task.root}"]


# ---------------------------------------------------------------- arms

def context(tmp_path: Path, **changes: Any) -> arms.ArmContext:
    base: Dict[str, Any] = {"workspace": tmp_path / "ws", "goal": "Do the thing.", "accept": "make test",
                            "timeout": 600, "max_cost": 6.0, "effort": "low",
                            "last_message": tmp_path / "last.txt", "skill_root": tmp_path / "skill",
                            "claude_home": tmp_path / "home" / ".claude", "duet_home": tmp_path / "state"}
    base.update(changes)
    return arms.ArmContext(**base)


def test_every_arm_gets_the_same_task_brief(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    brief = arms.task_brief(ctx)
    assert "Do the thing." in brief and "make test" in brief
    assert set(arms.ARMS) == {"solo-claude", "solo-codex", "duet-claude-host", "duet-codex-host"}
    for name, build in arms.ARMS.items():
        launch = build(ctx)
        assert brief in launch.prompt, name
        assert not any("Do the thing." in arg for arg in launch.argv), name
        assert launch.duet == name.startswith("duet")
        assert not any("dangerously" in arg or "bypass" in arg.lower() for arg in launch.argv), name


def test_claude_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(arms.CLAUDE_BIN_ENV, raising=False)
    solo = arms.solo_claude(context(tmp_path)).argv
    assert solo[:2] == ["claude", "-p"] and "--safe-mode" in solo
    assert solo[solo.index("--max-budget-usd") + 1] == "6" and solo[solo.index("--effort") + 1] == "low"
    assert solo[solo.index("--permission-mode") + 1] == "acceptEdits" and "--allowedTools" not in solo
    settings = json.loads(solo[solo.index("--settings") + 1])
    assert settings == {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}}

    host = arms.duet_claude_host(context(tmp_path, max_cost=None, effort=None)).argv
    prefix = f"python3 {tmp_path / 'skill' / 'scripts' / 'duet.py'}"
    rules = host[host.index("--allowedTools") + 1:host.index("--allowedTools") + 5]
    assert rules == [f"Bash({prefix} *)", f"Read(/{tmp_path / 'skill' / 'SKILL.md'})",
                     f"Read(/{tmp_path / 'state'}/**)", f"Edit(/{tmp_path / 'state'}/**)"]
    sandbox = json.loads(host[host.index("--settings") + 1])["sandbox"]
    assert sandbox["excludedCommands"] == [f"{prefix} *", f"{prefix}:*"]   # only the runner leaves the sandbox
    assert sandbox["filesystem"] == {"allowWrite": [str(tmp_path / "state")]}
    assert "--timeout 540" in arms.duet_prompt(context(tmp_path), "claude")
    assert "--timeout 90" in arms.duet_prompt(context(tmp_path, timeout=90), "codex")
    assert "--max-budget-usd" not in host and "--effort" not in host


def test_codex_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(arms.CODEX_BIN_ENV, raising=False)
    solo = arms.solo_codex(context(tmp_path)).argv
    assert solo[:2] == ["codex", "exec"] and solo[-1] == "-" and "--json" in solo
    assert 'sandbox_mode="workspace-write"' in solo and 'model_reasoning_effort="low"' in solo
    assert solo[solo.index("-o") + 1] == str(tmp_path / "last.txt")
    assert "sandbox_workspace_write.network_access=true" not in solo
    host = arms.duet_codex_host(context(tmp_path, effort=None)).argv
    assert host[host.index("--add-dir") + 1] == str(tmp_path / "home" / ".claude")
    assert str(tmp_path / "state") not in host
    (tmp_path / "state").mkdir()
    assert arms.duet_codex_host(context(tmp_path)).argv[-3:] == ["--add-dir", str(tmp_path / "state"), "-"]
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "elsewhere"))
    assert arms.default_duet_home() == tmp_path / "elsewhere"
    assert not any("model_reasoning_effort" in arg for arg in host)
    assert "--max-cost" not in arms.duet_prompt(context(tmp_path, max_cost=None), "codex")


def test_env_drops_the_other_labs_key() -> None:
    base = {"OPENAI_API_KEY": "a", "ANTHROPIC_API_KEY": "b", "ANTHROPIC_AUTH_TOKEN": "c", "NPM_TOKEN": "d", "PATH": "/bin"}
    assert arms.env_for("claude", base) == {"ANTHROPIC_API_KEY": "b", "ANTHROPIC_AUTH_TOKEN": "c", "PATH": "/bin"}
    assert arms.env_for("codex", base) == {"OPENAI_API_KEY": "a", "PATH": "/bin"}
    assert "PATH" in arms.env_for("codex")


# ---------------------------------------------------------------- usage parsing

def test_parse_claude_single_json() -> None:
    out = json.dumps({"result": "hi", "session_id": "s", "is_error": False, "total_cost_usd": 1.27,
                      "usage": {"input_tokens": 3, "output_tokens": 4, "service_tier": "x"}, "duration_ms": 5})
    used = usage.parse_claude(out)
    assert used.parsed and used.cost_usd == 1.27 and used.tokens == {"input_tokens": 3, "output_tokens": 4}
    assert used.session == "s" and used.final_message == "hi" and not used.reported_error


def test_parse_claude_stream_and_array_and_errors() -> None:
    stream = "\n".join([
        "warning: not json", json.dumps({"type": "system", "session_id": "s"}), "{broken",
        json.dumps({"type": "result", "is_error": True, "result": None, "session_id": "s", "total_cost_usd": "n/a"}),
    ])
    used = usage.parse_claude(stream)
    assert used.parsed and used.reported_error and used.cost_usd is None and used.final_message == ""
    array = json.dumps([{"type": "system"}, {"type": "result", "result": "ok", "session_id": "z", "total_cost_usd": 2}])
    assert usage.parse_claude(array).cost_usd == 2.0
    assert usage.parse_claude("") == usage.Usage() and not usage.parse_claude("plain text").parsed
    assert not usage.parse_claude("42").parsed


def test_parse_codex_sums_turns_and_reports_no_dollars() -> None:
    lines = [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"type": "command_execution", "text": "ignored"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "final"}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 1, "reasoning_output_tokens": 7}},
    ]
    used = usage.parse_codex("\n".join(json.dumps(line) for line in lines))
    assert used.cost_usd is None and used.session == "t1" and used.final_message == "final" and used.parsed
    assert used.tokens == {"input_tokens": 15, "cached_input_tokens": 4, "output_tokens": 3, "reasoning_output_tokens": 7}
    assert usage.parse_codex("", last_message=" from file \n").final_message == "from file"
    failed = usage.parse_codex(json.dumps({"type": "turn.failed", "error": {"message": "x"}}))
    assert failed.reported_error and failed.tokens == {}
    assert not usage.parse_codex("not json at all").parsed
    assert usage.parse_codex(json.dumps({"type": "turn.completed", "usage": None})).tokens == {}


# ---------------------------------------------------------------- process handling

def test_proc_run_reports_exit_and_missing_binary(tmp_path: Path) -> None:
    out, err = tmp_path / "o", tmp_path / "e"
    done = proc.run([sys.executable, "-c", "import sys; print(sys.stdin.read()); sys.exit(4)"], tmp_path, 30, out, err, "hello")
    assert done.exit_code == 4 and not done.timed_out and out.read_text().strip() == "hello"
    assert proc.run([sys.executable, "-c", "print(1)"], tmp_path, 30, out, err).exit_code == 0
    missing = proc.run([str(tmp_path / "nope")], tmp_path, 5, out, err)
    assert missing.exit_code == proc.EXIT_NOT_STARTED and "could not start" in missing.error


def test_proc_run_kills_a_child_that_ignores_sigterm(tmp_path: Path) -> None:
    script = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(60)"
    started = time.monotonic()
    done = proc.run([sys.executable, "-c", script], tmp_path, 1, tmp_path / "o", tmp_path / "e", grace=0.5)
    assert done.timed_out and done.exit_code is None and time.monotonic() - started < 20


# ---------------------------------------------------------------- summary

def record(arm: str, task: str = "t", seed: int = 1, visible: bool = True, hidden: bool = True, **more: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {"arm": arm, "task": task, "seed": seed, "visible": {"passed": visible},
                            "hidden": {"passed": hidden}, "seconds": 100.0, "cost_usd": 1.0,
                            "tokens": {"input_tokens": 900, "output_tokens": 100}, "is_duet": False,
                            "duet": None, "timed_out": False, "started_at": "2026-01-01T00:00:00"}
    base.update(more)
    return base


def test_summary_maths() -> None:
    records = [
        record("solo", "a", hidden=True, seconds=10, cost_usd=1.0),
        record("solo", "b", hidden=False, seconds=20, cost_usd=3.0),            # looked done, wasn't
        record("solo", "c", visible=False, hidden=False, seconds=30, cost_usd=None, tokens={}),
        record("solo", "d", hidden=True, seconds=40, cost_usd=None, timed_out=True),
        record("duet", "a", is_duet=True, duet={"finished": True}),
        record("duet", "b", is_duet=True, duet={"finished": "unknown"}, hidden=False),
        record("duet", "c", is_duet=True, duet={"finished": False}, visible=False, hidden=False),
    ]
    stats = summary.summarize(records)
    solo = stats["solo"]
    assert solo["runs"] == 4 and solo["hidden_pass_rate"] == 0.5 and solo["visible_pass_rate"] == 0.75
    assert solo["visible_pass_hidden_fail_rate"] == 0.25
    assert solo["mean_cost_usd"] == 2.0 and solo["cost_runs"] == 2
    assert solo["mean_seconds"] == 25.0 and solo["mean_tokens"] == 1000 and solo["token_runs"] == 3
    assert solo["timeouts"] == 1 and solo["duet_runs"] == 0
    duet = stats["duet"]
    assert duet["hidden_pass_rate"] == pytest.approx(1 / 3) and duet["visible_pass_hidden_fail_rate"] == pytest.approx(1 / 3)
    assert duet["duet_runs"] == 3 and duet["duet_finished"] == 1 and duet["duet_unknown"] == 1

    text = summary.format_summary(records)
    solo_line = next(line for line in text.splitlines() if line.startswith("solo"))
    assert solo_line.split()[:5] == ["solo", "4", "50%", "75%", "25%"] and "2.00 (n=2)" in solo_line
    duet_line = next(line for line in text.splitlines() if line.startswith("duet"))
    assert "1/3 (1 unknown)" in duet_line and "33%" in duet_line
    grid = summary.format_by_task(records)
    assert grid.splitlines()[0].split() == ["task", "duet", "solo"]
    assert next(line for line in grid.splitlines() if line.startswith("b")).split() == ["b", "Fv", "Fv"]
    assert next(line for line in grid.splitlines() if line.startswith("d")).split() == ["d", "-", "Pv"]


def test_summary_uses_the_latest_rerun_and_handles_nothing() -> None:
    old = record("solo", hidden=False, started_at="2026-01-01T00:00:00")
    new = record("solo", hidden=True, started_at="2026-01-02T00:00:00")
    assert summary.summarize([new, old])["solo"] == summary.arm_stats([new])
    assert summary.format_summary([]) == "No records found."
    empty = summary.arm_stats([])
    assert empty["hidden_pass_rate"] is None and empty["mean_cost_usd"] is None
    assert "-" in summary.format_summary([record("solo", cost_usd=None, tokens={}, seconds=None)])


def test_load_records(tmp_path: Path) -> None:
    write(tmp_path / "s1" / "records" / "a.json", json.dumps(record("solo")))
    write(tmp_path / "s2" / "records" / "b.json", json.dumps(record("duet")))
    write(tmp_path / "s2" / "records" / "c.json", json.dumps({"unrelated": True}))
    write(tmp_path / "s2" / "logs" / "x.json", "{}")
    assert sorted(r["arm"] for r in summary.load_records(tmp_path)) == ["duet", "solo"]
    assert summary.load_records(tmp_path / "absent") == []
    write(tmp_path / "s1" / "records" / "bad.json", "{")
    with pytest.raises(ValueError, match="unreadable record"):
        summary.load_records(tmp_path)


# ---------------------------------------------------------------- command line

def cli_args(task_dir: Path, tmp_path: Path, *extra: str) -> List[str]:
    return ["run", "--tasks-dir", str(task_dir.parent), "--results", str(tmp_path / "out"),
            "--skill-root", str(tmp_path / "skill"), "--session", "sess", *extra]


def test_dry_run_prints_commands_and_starts_nothing(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                    capsys: pytest.CaptureFixture) -> None:
    code = cli.main(cli_args(task_dir, tmp_path, "--tasks", "all", "--arms", "all", "--seeds", "2",
                             "--timeout", "600", "--max-cost", "6", "--effort", "low", "--dry-run", "--show-prompt"))
    out = capsys.readouterr().out
    assert code == 0 and "8 run(s) planned, nothing was started" in out
    assert out.count("command: ") == 8 and "adder__duet-codex-host__s2" in out
    assert "--max-budget-usd 6" in out and "model_reasoning_effort" in out and SECRET_PROMPT_MARK in out
    assert "600s wall clock" in out
    assert not fake_clients.exists() and not (tmp_path / "out").exists()


def test_run_command_end_to_end(task_dir: Path, tmp_path: Path, fake_clients: Path, monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(task_dir / "solutions" / "naive"))
    code = cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "solo-claude,solo-codex"))
    out = capsys.readouterr().out
    assert code == 0 and "2 run(s)" in out and "visible=pass hidden=FAIL" in out
    assert len(calls(fake_clients)) == 2                                   # one seed by default, nothing extra
    assert len(list((tmp_path / "out" / "sess" / "records").glob("*.json"))) == 2
    assert not list((tmp_path / "out" / "sess" / "work").iterdir())
    line = next(l for l in out.splitlines() if l.startswith("solo-claude"))
    assert line.split()[1:5] == ["1", "0%", "100%", "100%"]

    assert cli.main(["summary", "--results", str(tmp_path / "out"), "--by-task"]) == 0
    report = capsys.readouterr().out
    assert "looked done, wasn't" in report and "Fv" in report
    assert cli.main(["summary", "--results", str(tmp_path / "empty")]) == 1


def test_total_cost_cap_stops_the_matrix(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                         capsys: pytest.CaptureFixture) -> None:
    code = cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "solo-claude", "--seeds", "3",
                             "--max-total-cost", "0.5"))
    assert code == 1 and "Stopping before run 3" in capsys.readouterr().out
    assert len(calls(fake_clients)) == 2


def test_run_refuses_bad_requests(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                  capsys: pytest.CaptureFixture) -> None:
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "solo-gpt")) == 2
    assert "unknown arm" in capsys.readouterr().err
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "nope", "--arms", "all")) == 2
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", ",", "--arms", "all")) == 2
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "all", "--seeds", "0")) == 2
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "all", "--max-cost", "0")) == 2
    with pytest.raises(SystemExit):
        cli.main(["run", "--arms", "all"])                                  # --tasks is required: no silent "everything"
    capsys.readouterr()
    (tmp_path / "project" / ".git").mkdir(parents=True)
    code = cli.main(["run", "--tasks-dir", str(task_dir.parent), "--tasks", "adder", "--arms", "solo-claude",
                     "--results", str(tmp_path / "project" / "results")])
    assert code == 2 and "inside the repository" in capsys.readouterr().err
    assert not fake_clients.exists()


def test_list_and_verify_on_the_fixture(task_dir: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["list", "--tasks-dir", str(task_dir.parent)]) == 0
    assert "adder" in capsys.readouterr().out
    assert cli.main(["verify", "--tasks-dir", str(task_dir.parent)]) == 0
    assert "All tasks behave as designed." in capsys.readouterr().out
    write(task_dir / "solutions" / "naive" / "calc.py", "def add(a, b):\n    return a + b\n")
    __import__("shutil").rmtree(str(task_dir / "solutions" / "reference"))
    assert cli.main(["verify", "--tasks-dir", str(task_dir.parent)]) == 1
    out = capsys.readouterr().out
    assert "WRONG   adder/naive" in out and "MISSING adder/reference" in out and "2 problem(s)" in out


def test_shipped_tasks_behave_as_designed(capsys: pytest.CaptureFixture) -> None:
    """Every shipped task: the starter fails, the reference passes everything, and the naive
    solution passes the visible command while failing the hidden oracle."""
    names = [t.name for t in tasklib.discover(cli.DEFAULT_TASKS_DIR)]
    assert 4 <= len(names) <= 6
    assert cli.main(["verify"]) == 0
    out = capsys.readouterr().out
    assert out.count("ok ") == 3 * len(names) and "WRONG" not in out and "MISSING" not in out


def test_entry_script_runs(task_dir: Path) -> None:
    import subprocess
    done = subprocess.run([sys.executable, str(REPO / "eval" / "run_eval.py"), "list", "--tasks-dir", str(task_dir.parent)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "solo-claude" in done.stdout
