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

from evallib import arms, audit, cli, duetinfo, layout, plan, proc, report, runner, scoring, summary, usage  # noqa: E402
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
    for name in ("FAKE_EVAL_OVERLAY", "FAKE_EVAL_OVERLAY_CODEX", "FAKE_EVAL_SLEEP", "FAKE_EVAL_EXIT",
                 "FAKE_EVAL_DUET", "FAKE_EVAL_STATE_RUNS", "FAKE_EVAL_TAMPER", "FAKE_EVAL_STDERR",
                 "FAKE_EVAL_CHILD_PID", "FAKE_EVAL_REPLY"):
        monkeypatch.delenv(name, raising=False)
    return log


def options(tmp_path: Path, **changes: Any) -> runner.RunOptions:
    base: Dict[str, Any] = {"session_dir": tmp_path / "results" / "s1", "timeout": 60, "max_cost": 2.0,
                            "effort": "low", "skill_root": tmp_path / "skill",
                            "duet_home": tmp_path / "duet-home",
                            "work_root": tmp_path / "scratch" / "work"}
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
    assert str(opts.session_dir.resolve()) not in str(Path(call["cwd"]).resolve())   # nowhere near the results
    assert layout.steps_to_reach(Path(call["cwd"]), opts.session_dir) > layout.MIN_STEPS_APART
    assert "OPENAI_API_KEY" not in call["secrets"] and "GITHUB_TOKEN" not in call["secrets"]
    assert "ANTHROPIC_API_KEY" in call["secrets"]

    saved = json.loads((opts.session_dir / "records" / "adder__solo-claude__s1.json").read_text())
    assert saved == record
    assert not Path(call["cwd"]).exists() and record["workspace"] is None        # workspace thrown away
    logs = Path(record["logs"])
    assert (logs / "prompt.md").is_file() and "return a + b" in (logs / "diff.patch").read_text()
    assert not (logs / "copy").exists() and not (logs / "oracle").exists()       # scoring trees removed too
    assert record["version"] == runner.RECORD_VERSION and record["infra_failure"] is None
    assert record["host_tokens"] == 2000 + 9000 + 300 and record["peer_tokens"] is None
    assert "cached" in record["token_basis"]["host"] and record["token_basis"]["peer"] is None


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
    kept = Path(record["workspace"])
    assert kept.is_dir() and kept.parent.parent == (tmp_path / "results" / "s1" / "workspaces")
    assert not list((tmp_path / "scratch" / "work").iterdir())                   # nothing left for the next arm
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
    assert record["infra_failure"] is None                  # running out of time is counted, but not as infrastructure
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
    assert record["infra_failure"] == "the client could not be started"


def test_weakened_tests_are_restored_before_scoring(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EVAL_TAMPER", "tests/test_calc.py")
    record = runner.run_one(tasklib.load_task(task_dir), "solo-claude", 1, options(tmp_path))
    assert record["tampered"] == ["tests/test_calc.py"]
    assert not record["visible"]["passed"]                 # the real test ran, not the weakened one


def test_rerun_sets_the_previous_record_aside(task_dir: Path, tmp_path: Path, fake_clients: Path) -> None:
    task, opts = tasklib.load_task(task_dir), options(tmp_path)
    first = runner.run_one(task, "solo-claude", 1, opts)
    write(Path(first["logs"]) / "stale.txt", "old")
    second = runner.run_one(task, "solo-claude", 1, opts)
    assert not (Path(second["logs"]) / "stale.txt").exists()
    aside = opts.session_dir / "records" / runner.SUPERSEDED
    assert [p.name for p in aside.iterdir()] == ["adder__solo-claude__s1.1.json"]
    assert (opts.session_dir / "logs" / runner.SUPERSEDED / "adder__solo-claude__s1.1" / "stale.txt").is_file()
    runner.run_one(task, "solo-claude", 1, opts)
    assert len(list(aside.iterdir())) == 2
    session = summary.load_sessions(opts.session_dir)["s1"]
    assert len(session.records) == 1 and session.superseded == 2


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

def duet_state(**changes: Any) -> str:
    base: Dict[str, Any] = {"status": "done", "peer_turns": 5, "max_cost_usd": 6,
                            "peer_usage": {"cost_usd": 1.25, "input_tokens": 10, "output_tokens": 5}}
    base.update(changes)
    return json.dumps(base)


def test_duet_is_found_in_the_state_directory_by_its_workspace(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    task, opts = tasklib.load_task(task_dir), options(tmp_path)
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(task.solution("reference")))
    monkeypatch.setenv("FAKE_EVAL_STATE_RUNS", str(opts.duet_home / "runs"))
    monkeypatch.setenv("FAKE_EVAL_DUET", duet_state())
    # another workspace's run in the same state directory must not be mistaken for this one
    write(opts.duet_home / "runs" / "other" / "state.json",
          json.dumps({"status": "active", "peer_turns": 0, "workspace": str(tmp_path)}))
    record = runner.run_one(task, "duet-codex-host", 1, opts)
    duet = record["duet"]
    assert record["is_duet"] and duet["found"] == "state-dir" and duet["finished"] is True and duet["ran"] is True
    assert duet["peer_turns"] == 5 and duet["per_protocol"] is True and record["infra_failure"] is None
    assert record["peer_cost_usd"] == 1.25 and record["cost_usd"] == 1.25 and record["peer_tokens"] == 15
    assert record["host_tokens"] == 1500 + 75 and "uncached" in record["token_basis"]["peer"]
    assert (Path(record["logs"]) / "duet-run" / "state.json").is_file()
    assert record["limits"] == {"timeout": 300, "solo_timeout": 60, "max_cost": 2.0, "effort": "low",
                                "effort_note": arms.CLAUDE_PEER_EFFORT_NOTE, "duet_turns": 8}
    call = calls(fake_clients)[0]
    assert "--peer claude" in call["stdin"] and "--max-cost 2" in call["stdin"] and "--max-turns 8" in call["stdin"]
    assert str(tmp_path / "skill" / "SKILL.md") in call["stdin"] and "--timeout 300" in call["stdin"]
    assert "sandbox_workspace_write.network_access=true" in call["argv"] and "--add-dir" in call["argv"]


def test_duet_run_inside_the_workspace_is_the_fallback(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EVAL_DUET", duet_state(
        status="active", peer_turns=2, peer_usage={"cost_usd": 0.0, "input_tokens": 700, "output_tokens": 50}))
    record = runner.run_one(tasklib.load_task(task_dir), "duet-claude-host", 1, options(tmp_path))
    duet = record["duet"]
    assert duet["found"] == "workspace" and duet["finished"] is False and duet["status"] == "active"
    assert duet["ran"] is True and duet["per_protocol"] is False and record["infra_failure"] is None
    assert record["peer_cost_usd"] is None                             # a Codex peer reports no dollars
    assert record["cost_usd"] == pytest.approx(0.4321) and record["peer_tokens"] == 750
    assert "cached included" in record["token_basis"]["peer"] and record["limits"]["effort_note"] == ""
    assert (Path(record["logs"]) / "duet" / "run-1" / "state.json").is_file()
    call = calls(fake_clients)[0]
    runner_path = str(tmp_path / "skill" / "scripts" / "duet.py")
    assert f"Bash(python3 {runner_path} *)" in call["argv"]
    assert "--peer-effort low" in call["stdin"] and "--peer claude" not in call["stdin"]
    settings = json.loads(call["argv"][call["argv"].index("--settings") + 1])
    assert f"Read(/{(tmp_path / 'results' / 's1').resolve()}/**)" in settings["permissions"]["deny"]


def test_duet_report_in_the_final_message_is_the_last_resort(tmp_path: Path) -> None:
    message = "Finished.\n```json\n" + duet_state(run="/somewhere/r1") + "\n```"
    outcome = duetinfo.duet_outcome(tmp_path, message, tmp_path / "state")
    assert outcome["found"] == "final-message" and outcome["finished"] is True and outcome["ran"] is True
    assert outcome["run_folder"] == "/somewhere/r1" and outcome["peer_cost_usd"] == 1.25
    vague = duetinfo.duet_outcome(tmp_path, json.dumps({"status": "active"}), tmp_path / "state")
    assert vague["finished"] is False and vague["ran"] == "unknown" and vague["per_protocol"] is False


def test_no_duet_is_an_infrastructure_failure_not_a_model_failure(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    task = tasklib.load_task(task_dir)
    monkeypatch.setenv("FAKE_EVAL_REPLY", "Stopped. Duet `init` exited 2: [Errno 1] Operation not permitted: 'exclude'")
    record = runner.run_one(task, "duet-codex-host", 1, options(tmp_path))
    duet = record["duet"]
    assert record["status"] == "ok" and duet["found"] is None and duet["ran"] is False and duet["finished"] == "unknown"
    assert "no duet took place" in record["infra_failure"] and "operation not permitted" in record["infra_failure"]
    # a run that was started but never reached the peer is not a duet either
    monkeypatch.setenv("FAKE_EVAL_DUET", duet_state(status="active", peer_turns=0))
    monkeypatch.setenv("FAKE_EVAL_REPLY", "I finished the task myself.")
    record = runner.run_one(task, "duet-codex-host", 2, options(tmp_path))
    assert record["duet"]["ran"] is False and record["infra_failure"] == "no duet took place: the peer never took a turn"


def test_duet_outcome_ignores_unreadable_files(tmp_path: Path) -> None:
    state = tmp_path / "state"
    write(tmp_path / ".duet" / "r" / "broken.json", "{oops")
    write(tmp_path / ".duet" / "r" / "list.json", "[1, 2]")
    write(tmp_path / ".duet" / "r" / "other.json", json.dumps({"no_status": 1}))
    write(state / "runs" / "bad" / "state.json", "{oops")
    write(state / "runs" / "list" / "state.json", "[1]")
    write(state / "runs" / "stray.txt", "not a run")
    nothing = duetinfo.duet_outcome(tmp_path, "text with {braces} and {\"a\": [1]}", state)
    assert nothing["finished"] == "unknown" and nothing["ran"] is False and nothing["peer_cost_usd"] is None
    write(tmp_path / ".duet" / "r" / "state.json",
          json.dumps({"status": "DONE", "peer_turns": True, "peer_usage": {"cost_usd": "n/a"}}))
    outcome = duetinfo.duet_outcome(tmp_path, "", state)
    assert outcome["finished"] is True and outcome["peer_cost_usd"] is None and outcome["peer_turns"] is None
    assert outcome["ran"] == "unknown" and outcome["peer_tokens"] is None


# ---------------------------------------------------------------- leaks: layout, order, audit

def test_steps_between_a_workspace_and_other_folders(tmp_path: Path) -> None:
    workspace = tmp_path / "a" / "work" / "run" / "ws"
    assert layout.steps_to_reach(workspace, tmp_path / "a" / "work" / "run" / "logs") == 1
    assert layout.steps_to_reach(workspace, tmp_path / "results") == 4
    assert layout.too_close(tmp_path / "a" / "work", tmp_path / "a" / "logs")
    assert not layout.too_close(tmp_path / "a" / "work", tmp_path / "results")
    assert layout.default_work_root().name == "work" and str(os.getuid()) in layout.default_work_root().parent.name
    (tmp_path / "project" / ".git").mkdir(parents=True)
    assert layout.in_git_repository(tmp_path / "project" / "deep" / "er") == (tmp_path / "project").resolve()


def test_run_refuses_a_work_folder_near_the_results(task_dir: Path, tmp_path: Path, fake_clients: Path) -> None:
    near = options(tmp_path, work_root=tmp_path / "results" / "s1" / "work")
    with pytest.raises(tasklib.TaskError, match="too close"):
        runner.run_one(tasklib.load_task(task_dir), "solo-claude", 1, near)
    watched = options(tmp_path, watch=(tmp_path / "scratch" / "tasks",))
    with pytest.raises(tasklib.TaskError, match="too close"):
        runner.run_one(tasklib.load_task(task_dir), "solo-claude", 1, watched)
    assert not fake_clients.exists()


def test_arm_order_is_seeded_and_no_arm_always_runs_last(task_dir: Path) -> None:
    task, names = tasklib.load_task(task_dir), ["a", "b", "c", "d"]
    first = plan.make_plan([task], names, 40, 7)
    assert first == plan.make_plan([task], names, 40, 7) and first != plan.make_plan([task], names, 40, 8)
    assert len(first) == 160 and [run.position for run in first[:4]] == [1, 2, 3, 4] and first[0].of == 4
    for seed in range(1, 41):
        assert sorted(run.arm for run in first if run.seed == seed) == names
    assert {run.arm for run in first if run.position == 4} == set(names)
    assert {run.arm for run in first if run.position == 1} == set(names)
    assert isinstance(plan.new_order_seed(), int)


def test_leak_audit_catches_absolute_and_relative_paths(task_dir: Path, tmp_path: Path) -> None:
    task, results = tasklib.load_task(task_dir), tmp_path / "results"
    clean = write(tmp_path / "out" / "stdout.txt", "ran pytest in the workspace; wrote tests/test_more.py")
    absolute = write(tmp_path / "out" / "duet" / "turn.txt", f"cat {task.hidden}/test_calc_hidden.py")
    relative = write(tmp_path / "out" / "stderr.txt",
                     "cat ../../../x/eval/tasks/adder/hidden/*.py\nless solutions/reference/calc.py")
    by_name = write(tmp_path / "out" / "last_message.txt", "I found test_calc_hidden.py and read it")
    logs = write(tmp_path / "out" / "x.txt", f"ls {results}/s1/logs")
    assert audit.oracle_paths_seen(task, [clean, tmp_path / "absent"], [results]) == []
    seen = audit.oracle_paths_seen(task, [clean, absolute.parent, relative, by_name, logs], [results])
    assert f"turn.txt: {task.root}" in seen
    assert "stderr.txt: adder/hidden" in seen and "stderr.txt: solutions/reference" in seen
    assert "last_message.txt: test_calc_hidden.py" in seen and f"x.txt: {results}" in seen
    assert audit.outside_paths_seen([clean, tmp_path / "absent"]) == []
    assert audit.outside_paths_seen([relative]) == ["stderr.txt: ../../../x/eval/tasks/adder/hidden/"]
    big = write(tmp_path / "out" / "big.txt", "../secret " * (audit.MAX_SCAN_BYTES // 8))
    assert audit.outside_paths_seen([big]) == []                          # too large to scan is skipped, not read


def test_run_records_leak_flags(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EVAL_REPLY", "I ran cat ../../logs/other/hidden.log and read test_calc_hidden.py")
    record = runner.run_one(tasklib.load_task(task_dir), "solo-claude", 1, options(tmp_path))
    assert any("test_calc_hidden.py" in hit for hit in record["oracle_paths_seen"])
    assert any("../../logs/other/hidden.log" in hit for hit in record["outside_paths_seen"])
    monkeypatch.delenv("FAKE_EVAL_REPLY")
    clean = runner.run_one(tasklib.load_task(task_dir), "solo-codex", 1, options(tmp_path))
    assert clean["oracle_paths_seen"] == [] and clean["outside_paths_seen"] == []


def test_staged_skill_leaves_the_tasks_and_tests_behind(tmp_path: Path) -> None:
    skill = tmp_path / "checkout"
    for name in ("SKILL.md", "scripts/duet.py", "scripts/duetlib/cli.py", "eval/tasks/t/hidden/test_h.py",
                 "tests/test_x.py", ".git/HEAD", "scripts/__pycache__/x.pyc"):
        write(skill / name, "x")
    staged = layout.stage_skill(skill, tmp_path / "stage" / "skill")
    assert sorted(p.name for p in staged.iterdir()) == ["SKILL.md", "scripts"]
    assert (staged / "scripts" / "duetlib" / "cli.py").is_file() and not list(staged.rglob("__pycache__"))
    write(staged / "stale.txt", "old")
    assert not (layout.stage_skill(skill, staged) / "stale.txt").exists()
    with pytest.raises(tasklib.TaskError, match="SKILL.md"):
        layout.stage_skill(tmp_path / "nowhere", tmp_path / "stage" / "other")


def test_each_duet_run_gets_a_fresh_copy_of_the_skill(task_dir: Path, tmp_path: Path, fake_clients: Path) -> None:
    task, source, staged = tasklib.load_task(task_dir), tmp_path / "checkout", tmp_path / "scratch" / "skill-s1"
    write(source / "SKILL.md", "real")
    write(source / "scripts" / "duet.py", "real")
    edited = write(staged / "scripts" / "duet.py", "edited by an earlier arm")
    opts = options(tmp_path, skill_root=staged, skill_source=source)
    runner.run_one(task, "solo-claude", 1, opts)
    assert edited.read_text() == "edited by an earlier arm"               # solo arms have no use for the skill
    runner.run_one(task, "duet-codex-host", 1, opts)
    assert edited.read_text() == "real" and str(staged / "SKILL.md") in calls(fake_clients)[1]["stdin"]


def test_staged_tasks_are_read_from_the_copy(task_dir: Path, tmp_path: Path) -> None:
    original = tasklib.load_task(task_dir)
    staged = layout.stage_tasks([original], tmp_path / "session" / "tasks")
    assert [t.root for t in staged] == [(tmp_path / "session" / "tasks" / "adder").resolve()]
    assert staged[0].goal == original.goal and (staged[0].hidden / "test_calc_hidden.py").is_file()
    assert layout.stage_tasks([original], tmp_path / "session" / "tasks")[0].accept == ACCEPT   # restaging is fine


# ---------------------------------------------------------------- scoring isolation

FORCE_PASS = "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n"


def scored(task_dir: Path, tmp_path: Path, files: Dict[str, str], solution: str = "naive",
           **config: str) -> scoring.Score:
    write(task_dir / "task.json", json.dumps({"accept": ACCEPT, "protected": ["tests"], **config}))
    task, workspace = tasklib.load_task(task_dir), tmp_path / "ws-score"
    tasklib.prepare_workspace(task, workspace)
    tasklib.copy_tree(task.solution(solution), workspace)
    for name, text in files.items():
        write(workspace / name, text)
    return scoring.score_workspace(task, workspace, tmp_path / "score")


def test_workspace_conftest_cannot_force_the_oracle(task_dir: Path, tmp_path: Path) -> None:
    score = scored(task_dir, tmp_path, {"conftest.py": FORCE_PASS, "sub/conftest.py": FORCE_PASS,
                                        "pytest.ini": "[pytest]\naddopts = --co\n"})
    assert not score.hidden.passed and score.hidden.tests_failed == 1 and score.hidden.tests_passed == 1
    assert score.runner_files_added == ["conftest.py", "pytest.ini", "sub/conftest.py"]
    assert score.as_record()["runner_files_added"] == score.runner_files_added


def test_added_test_files_are_flagged_and_left_out(task_dir: Path, tmp_path: Path) -> None:
    score = scored(task_dir, tmp_path, {"tests/test_extra.py": "def test_bad():\n    assert False\n",
                                        "tests/conftest.py": FORCE_PASS}, solution="reference")
    assert score.tampered == ["tests/conftest.py", "tests/test_extra.py"]
    assert score.visible.passed and score.visible.tests_passed == 1 and score.hidden.passed


def test_parsed_failures_fail_even_when_the_command_exits_zero(task_dir: Path, tmp_path: Path) -> None:
    score = scored(task_dir, tmp_path, {}, "reference", hidden_cmd="echo '1 failed, 2 passed in 0.1s'")
    assert score.hidden.exit_code == 0 and not score.hidden.passed and score.hidden.tests_failed == 1


def test_oracle_tree_holds_only_source_and_hidden_tests(task_dir: Path, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "GITHUB_TOKEN", "MY_SERVICE_SECRET"):
        monkeypatch.setenv(name, "sk-live")
    check = ("test -d hidden_tests && test -f calc.py && test -f pytest.ini && test ! -e tests "
             "&& test ! -e conftest.py && test ! -e test_mine.py && test ! -e setup.cfg "
             "&& test -z \"$OPENAI_API_KEY$GITHUB_TOKEN$MY_SERVICE_SECRET\" && test -n \"$PATH\"")
    score = scored(task_dir, tmp_path, {"conftest.py": "", "test_mine.py": "", "setup.cfg": ""}, "reference",
                   hidden_cmd=check)
    assert score.hidden.passed, score.hidden.tail
    env = scoring.scrub_env({"PATH": "/bin", "ANTHROPIC_API_KEY": "a", "aws_secret_access_key": "b",
                             "SSH_AUTH_SOCK": "c", "DB_PASSWORD": "d", "HOME": "/h"})
    assert env == {"PATH": "/bin", "HOME": "/h"} and "PATH" in scoring.scrub_env()


# ---------------------------------------------------------------- infrastructure failures, best of two

def test_infrastructure_failures_are_told_apart_from_model_failures(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    task = tasklib.load_task(task_dir)
    wrong = runner.run_one(task, "solo-claude", 1, options(tmp_path))          # ran, solved nothing
    assert wrong["status"] == "ok" and wrong["infra_failure"] is None
    monkeypatch.setenv("FAKE_EVAL_EXIT", "3")
    crashed = runner.run_one(task, "solo-claude", 2, options(tmp_path))
    assert crashed["infra_failure"] == "the client failed before doing any work"
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(task.solution("naive")))
    worked = runner.run_one(task, "solo-claude", 3, options(tmp_path))         # failed, but only after working
    assert worked["status"] == "error" and worked["infra_failure"] is None
    monkeypatch.setenv("FAKE_EVAL_STDERR", "Error: you have hit your usage limit. Try again at 6pm.")
    limited = runner.run_one(task, "solo-codex", 4, options(tmp_path))
    assert limited["infra_failure"] == "the client failed: usage limit"


def test_best_of_two_picks_the_member_that_passes_the_visible_check(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    task = tasklib.load_task(task_dir)
    monkeypatch.setenv("FAKE_EVAL_OVERLAY_CODEX", str(task.solution("reference")))   # Claude leaves the starter
    record = runner.run_one(task, arms.BEST_OF_TWO, 1, options(tmp_path, timeout=61, max_cost=3.0))
    assert record["picked"] == "solo-codex" and record["visible"]["passed"] and record["hidden"]["passed"]
    assert [member["arm"] for member in record["members"]] == ["solo-claude", "solo-codex"]
    assert record["cost_usd"] == pytest.approx(0.4321) and not record["is_duet"] and record["duet"] is None
    assert record["seconds"] == pytest.approx(sum(member["seconds"] for member in record["members"]), abs=0.02)
    assert record["limits"]["timeout"] == 30 and record["limits"]["max_cost"] == 1.5
    assert record["host_tokens"] == (2000 + 9000 + 300) + (1500 + 75) and record["infra_failure"] is None
    claude, codex = calls(fake_clients)
    assert claude["argv"][claude["argv"].index("--max-budget-usd") + 1] == "1.5" and codex["codex"]
    assert claude["cwd"] != codex["cwd"]
    assert (Path(record["logs"]) / "solo-codex" / "diff.patch").is_file()
    saved = json.loads((tmp_path / "results" / "s1" / "records" / "adder__best-of-two-solo__s1.json").read_text())
    assert saved == record


def test_best_of_two_breaks_a_tie_with_the_smaller_diff(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    task = tasklib.load_task(task_dir)
    long_way = write(tmp_path / "big" / "calc.py", "def add(a, b):\n    total = a\n    total = total + b\n    return total\n")
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(long_way.parent))
    monkeypatch.setenv("FAKE_EVAL_OVERLAY_CODEX", str(task.solution("reference")))
    record = runner.run_one(task, arms.BEST_OF_TWO, 1, options(tmp_path))
    assert all(member["visible"]["passed"] for member in record["members"]) and record["picked"] == "solo-codex"
    assert runner.diff_size("--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n-old\n+new\n+more\n unchanged\n") == 3


def test_best_of_two_with_a_missing_client_is_an_infrastructure_failure(task_dir: Path, tmp_path: Path,
                                                                        fake_clients: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    task = tasklib.load_task(task_dir)
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(task.solution("reference")))
    monkeypatch.setenv(arms.CODEX_BIN_ENV, str(tmp_path / "no-such-client"))
    record = runner.run_one(task, arms.BEST_OF_TWO, 1, options(tmp_path))
    assert record["picked"] == "solo-claude" and record["hidden"]["passed"] and record["status"] == "not_started"
    assert record["infra_failure"] == "solo-codex: the client could not be started"


# ---------------------------------------------------------------- arms

def context(tmp_path: Path, **changes: Any) -> arms.ArmContext:
    base: Dict[str, Any] = {"workspace": tmp_path / "ws", "goal": "Do the thing.", "accept": "make test",
                            "timeout": 600, "max_cost": 6.0, "effort": "low",
                            "last_message": tmp_path / "last.txt", "skill_root": tmp_path / "skill",
                            "claude_home": tmp_path / "home" / ".claude", "duet_home": tmp_path / "state"}
    base.update(changes)
    return arms.ArmContext(**base)


def test_every_arm_gets_the_same_task_brief_and_quality_bar(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    brief = arms.task_brief(ctx)
    assert "Do the thing." in brief and "make test" in brief
    assert "line by line" in brief and "Try at least one case" in brief
    assert set(arms.ARMS) == {"solo-claude", "solo-codex", "duet-claude-host", "duet-codex-host"}
    assert arms.arm_names() == [*arms.ARMS, arms.BEST_OF_TWO]
    assert arms.COMPOSITE_ARMS[arms.BEST_OF_TWO] == ("solo-claude", "solo-codex")
    for name, build in arms.ARMS.items():
        launch = build(ctx)
        assert brief in launch.prompt, name
        rest = launch.prompt.replace(brief, "")
        assert "line by line" not in rest and "Try at least one case" not in rest, name   # the quality bar is shared
        assert not any("Do the thing." in arg for arg in launch.argv), name
        assert launch.duet == name.startswith("duet") == arms.is_duet(name)
        assert not any("dangerously" in arg or "bypass" in arg.lower() for arg in launch.argv), name


def test_budgets_follow_the_shape_of_the_arm(tmp_path: Path) -> None:
    assert arms.duet_timeout(900, 8) == 4500 and arms.duet_timeout(60, 1) == 90
    assert arms.arm_timeout("solo-codex", 900, 8) == 900 and arms.arm_timeout("duet-claude-host", 900, 8) == 4500
    assert arms.arm_timeout("duet-codex-host", 900, 8, duet_override=1200) == 1200
    assert arms.arm_timeout(arms.BEST_OF_TWO, 900, 8) == 450 and arms.arm_timeout(arms.BEST_OF_TWO, 1, 8) == 1
    assert arms.arm_max_cost(arms.BEST_OF_TWO, 6.0) == 3.0 and arms.arm_max_cost(arms.BEST_OF_TWO, None) is None
    assert arms.arm_max_cost("solo-claude", 6.0) == 6.0
    assert "4500s" in arms.timeout_note("duet-claude-host", 900, 8) and "half" in arms.timeout_note(arms.BEST_OF_TWO, 900, 8)
    assert arms.timeout_note("solo-claude", 900, 8) == ""
    with_claude_peer = arms.duet_codex_host(context(tmp_path))
    assert with_claude_peer.effort_note == arms.CLAUDE_PEER_EFFORT_NOTE and "--peer-effort" not in with_claude_peer.prompt
    assert arms.duet_codex_host(context(tmp_path, effort=None)).effort_note == ""
    assert arms.duet_claude_host(context(tmp_path)).effort_note == "" and arms.solo_claude(context(tmp_path)).effort_note == ""
    basis = arms.token_basis(with_claude_peer)
    assert "cached included" in basis["host"] and "uncached" in basis["peer"]
    assert "cached included" in arms.token_basis(arms.duet_claude_host(context(tmp_path)))["peer"]
    assert arms.token_basis(arms.solo_codex(context(tmp_path)))["peer"] is None


def test_claude_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(arms.CLAUDE_BIN_ENV, raising=False)
    solo = arms.solo_claude(context(tmp_path)).argv
    assert solo[:2] == ["claude", "-p"] and "--safe-mode" in solo
    assert solo[solo.index("--max-budget-usd") + 1] == "6" and solo[solo.index("--effort") + 1] == "low"
    assert solo[solo.index("--permission-mode") + 1] == "acceptEdits" and "--allowedTools" not in solo
    settings = json.loads(solo[solo.index("--settings") + 1])
    assert settings == {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}}
    guarded = arms.solo_claude(context(tmp_path, deny_read=(tmp_path / "results", tmp_path / "tasks"))).argv
    assert json.loads(guarded[guarded.index("--settings") + 1])["permissions"] == {
        "deny": [f"Read(/{tmp_path / 'results'}/**)", f"Read(/{tmp_path / 'tasks'}/**)"]}

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


def mixed_records() -> List[Dict[str, Any]]:
    return [
        record("solo", "a", hidden=True, seconds=10, cost_usd=1.0),
        record("solo", "b", hidden=False, seconds=20, cost_usd=3.0),            # looked done, wasn't
        record("solo", "c", visible=False, hidden=False, seconds=30, cost_usd=None, tokens={}),
        record("solo", "d", hidden=True, seconds=40, cost_usd=None, timed_out=True, host_tokens=1000),
        record("solo", "e", visible=False, hidden=False, seconds=0, cost_usd=None, tokens={},
               infra_failure="the client could not be started"),
        record("duet", "a", is_duet=True, duet={"finished": True, "peer_turns": 4}, peer_tokens=50),
        record("duet", "b", is_duet=True, duet={"finished": "unknown", "peer_turns": None}, hidden=False),
        record("duet", "c", is_duet=True, duet={"finished": False, "peer_turns": 3}, visible=False, hidden=False,
               oracle_paths_seen=["stdout.txt: adder/hidden"]),
        record("duet", "d", is_duet=True, duet={"finished": True, "peer_turns": 0}),   # the host worked alone
    ]


def test_summary_counts_rates_and_intervals() -> None:
    stats = summary.summarize(mixed_records())
    solo = stats["solo"]
    assert solo["runs"] == 5 and solo["infra"] == 1 and solo["valid"] == 4 and solo["timeouts"] == 1
    assert solo["hidden_pass"] == 2 and solo["visible_pass"] == 3 and solo["false_done"] == 1
    assert solo["mean_cost_usd"] == 2.0 and solo["cost_runs"] == 2
    assert solo["mean_seconds"] == 25.0 and solo["mean_host_tokens"] == 1000 and solo["mean_peer_tokens"] is None
    low, high = solo["hidden_ci"]
    assert low == pytest.approx(0.150, abs=0.001) and high == pytest.approx(0.850, abs=0.001)
    assert solo["duet_runs"] == 0 and solo["per_protocol_runs"] == 0 and solo["leak_flagged"] == 0
    duet = stats["duet"]
    assert duet["hidden_pass"] == 2 and duet["duet_runs"] == 4 and duet["duet_finished"] == 2 and duet["duet_unknown"] == 1
    assert duet["per_protocol_runs"] == 1 and duet["per_protocol_hidden_pass"] == 1
    assert duet["mean_peer_tokens"] == 50 and duet["leak_flagged"] == 1
    assert summary.wilson(0, 0) is None and summary.wilson(0, 1)[0] == 0.0 and summary.wilson(5, 5)[1] == 1.0
    empty = summary.arm_stats([])
    assert empty["hidden_ci"] is None and empty["mean_cost_usd"] is None and empty["valid"] == 0
    old = record("solo", status="not_started")                                # a record from before version 2
    assert summary.arm_stats([old])["infra"] == 1


def test_summary_text_shows_counts_and_warnings() -> None:
    text = report.format_summary(mixed_records(), title="Session s1")
    assert text.splitlines()[0] == "Session s1"
    results_row, cost_row = [line for line in text.splitlines() if line.startswith("solo")]
    assert results_row.split()[:4] == ["solo", "5", "1", "1"]
    for cell in ("2/4 (50%)", "15-85%", "3/4 (75%)", "1/4 (25%)"):
        assert cell in results_row
    assert "2.00 (n=2)" in cost_row and "1,000" in cost_row and "25" in cost_row
    duet_row = next(line for line in text.splitlines() if line.startswith("duet"))
    assert "2/4 (1 unknown)" in duet_row and "1/1 (100%)" in duet_row
    assert "UNBALANCED" in text and "duet has 4 of 5" in text
    assert "not evidence of a difference" in text and "1 run(s) named the oracle" in text
    assert "never added together" in text
    assert report.format_summary([]) == "No records found."
    assert "-" in report.format_summary([record("solo", cost_usd=None, tokens={}, seconds=None)])
    balanced = report.format_summary([record("solo"), record("duet")], notes=["3 superseded record(s) are not counted."])
    assert "UNBALANCED" not in balanced and "3 superseded" in balanced

    grid = report.format_by_task(mixed_records())
    assert grid.splitlines()[0].split() == ["task", "duet", "solo"]
    assert next(line for line in grid.splitlines() if line.startswith("b")).split() == ["b", "Fv", "Fv"]
    assert next(line for line in grid.splitlines() if line.startswith("e")).split() == ["e", "-", "I"]
    repeated = report.format_by_task([record("solo", hidden=False), record("solo", visible=False, hidden=False)])
    assert "Fv F" in repeated                                                 # every record is shown, none replaced


def test_sessions_are_reported_apart_and_merged_only_on_request(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    limits = {"timeout": 900, "duet_timeout": 4500, "max_cost": 6.0, "effort": "low", "duet_turns": 8}
    write(tmp_path / "s1" / "records" / "a.json", json.dumps(record("solo", hidden=False, session_limits=limits)))
    write(tmp_path / "s1" / "session.json", json.dumps({"invocations": [
        {"order_seed": 3, "planned": 6, "completed": 1, "stopped_early": "reported spend reached --max-total-cost"}]}))
    write(tmp_path / "s2" / "records" / "a.json", json.dumps(record("solo", hidden=True, session_limits=limits)))
    write(tmp_path / "s2" / "records" / "c.json", json.dumps({"unrelated": True}))
    write(tmp_path / "s2" / "records" / runner.SUPERSEDED / "a.1.json", json.dumps(record("solo")))
    write(tmp_path / "s2" / "logs" / "x.json", "{}")
    write(tmp_path / "s2" / "session.json", "{broken")
    sessions = summary.load_sessions(tmp_path)
    assert sorted(sessions) == ["s1", "s2"] and len(sessions["s2"].records) == 1 and sessions["s2"].superseded == 1
    assert sessions["s1"].stopped_early == ["reported spend reached --max-total-cost"] and sessions["s2"].stopped_early == []
    assert list(summary.load_sessions(tmp_path / "s1")) == ["s1"] and summary.load_sessions(tmp_path / "absent") == {}

    assert cli.main(["summary", "--results", str(tmp_path)]) == 0
    apart = capsys.readouterr().out
    assert apart.count("mean $ reported:") == 1                            # the column notes are printed once
    assert apart.count("Session ") == 2 and "0/1 (0%)" in apart and "1/1 (100%)" in apart   # neither hides the other
    assert "UNBALANCED: this session stopped early" in apart and "1 superseded record(s)" in apart
    assert cli.main(["summary", "--results", str(tmp_path), "--merge", "--by-task"]) == 0
    merged = capsys.readouterr().out
    assert "1/2 (50%)" in merged and "2 sessions merged" in merged and "more than one session" in merged
    assert "Fv Pv" in merged or "Pv Fv" in merged

    write(tmp_path / "s3" / "records" / "a.json",
          json.dumps(record("solo", session_limits={**limits, "effort": "high"})))
    assert cli.main(["summary", "--results", str(tmp_path), "--merge"]) == 2
    assert "different limits" in capsys.readouterr().err
    assert cli.main(["summary", "--results", str(tmp_path), "--merge", "--allow-mixed-limits"]) == 0
    assert "MIXED LIMITS" in capsys.readouterr().out
    old = record("solo", limits={"timeout": 600, "max_cost": 6.0, "effort": "low", "duet_turns": None})
    assert summary.limits_key(old) == summary.limits_key(record("duet", limits={**old["limits"], "duet_turns": 8}))
    write(tmp_path / "s1" / "records" / "bad.json", "{")
    assert cli.main(["summary", "--results", str(tmp_path)]) == 2
    assert "unreadable record" in capsys.readouterr().err


# ---------------------------------------------------------------- command line

def cli_args(task_dir: Path, tmp_path: Path, *extra: str) -> List[str]:
    return ["run", "--tasks-dir", str(task_dir.parent), "--results", str(tmp_path / "out"),
            "--work-dir", str(tmp_path / "scratch" / "work"),
            "--skill-root", str(tmp_path / "skill"), "--session", "sess", *extra]


def test_dry_run_prints_commands_and_starts_nothing(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                    capsys: pytest.CaptureFixture) -> None:
    args = cli_args(task_dir, tmp_path, "--tasks", "all", "--arms", "all", "--seeds", "2", "--timeout", "600",
                    "--max-cost", "6", "--effort", "low", "--dry-run", "--show-prompt", "--order-seed", "5")
    code = cli.main(args)
    out = capsys.readouterr().out
    assert code == 0 and "10 run(s) planned, nothing was started" in out
    assert out.count("command: ") == 12 and "adder__duet-codex-host__s2" in out   # best-of-two starts two clients
    assert "--max-budget-usd 6" in out and "--max-budget-usd 3" in out and "model_reasoning_effort" in out
    assert SECRET_PROMPT_MARK in out and "order seed 5" in out
    assert "600s wall clock" in out and "3000s wall clock" in out and "300s wall clock" in out
    assert "duet arms get 3000s" in out and arms.CLAUDE_PEER_EFFORT_NOTE in out
    assert str(task_dir) not in out and str(tmp_path / "skill") not in out   # no prompt names the checkout or the tasks
    assert str(tmp_path / "scratch" / "skill-sess" / "SKILL.md") in out
    assert not fake_clients.exists() and not (tmp_path / "out").exists() and not (tmp_path / "scratch").exists()
    assert cli.main(args) == 0 and capsys.readouterr().out == out          # the same seed gives the same order
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "all", "--arms", "duet-claude-host", "--dry-run",
                             "--duet-timeout", "1234")) == 0
    assert "1234s wall clock" in capsys.readouterr().out


def test_run_command_end_to_end(task_dir: Path, tmp_path: Path, fake_clients: Path, monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setenv("FAKE_EVAL_OVERLAY", str(task_dir / "solutions" / "naive"))
    code = cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "solo-claude,solo-codex",
                             "--order-seed", "11"))
    out = capsys.readouterr().out
    session = tmp_path / "out" / "sess"
    assert code == 0 and "2 run(s)" in out and "visible=pass hidden=FAIL" in out and "order seed 11" in out
    assert len(calls(fake_clients)) == 2                                   # one seed by default, nothing extra
    records = [json.loads(p.read_text()) for p in sorted((session / "records").glob("*.json"))]
    assert len(records) == 2 and not list((tmp_path / "scratch" / "work").iterdir())
    assert sorted(r["order"]["position"] for r in records) == [1, 2]
    assert all(r["order"] == {"seed": 11, "position": r["order"]["position"], "of": 2} for r in records)
    assert all(r["eval_session"] == "sess" and r["session_limits"]["duet_timeout"] == 4500 for r in records)
    manifest = json.loads((session / "session.json").read_text())["invocations"]
    assert len(manifest) == 1 and manifest[0]["order_seed"] == 11 and manifest[0]["completed"] == 2
    assert manifest[0]["stopped_early"] is None and len(manifest[0]["planned"]) == 2
    assert (session / "tasks" / "adder" / "hidden" / "test_calc_hidden.py").is_file()   # read from a copy
    assert all(str(task_dir) not in call["stdin"] + " ".join(call["argv"]) for call in calls(fake_clients))
    line = next(l for l in out.splitlines() if l.startswith("solo-claude"))
    assert "0/1 (0%)" in line and line.count("1/1 (100%)") == 2

    assert cli.main(["summary", "--results", str(tmp_path / "out"), "--by-task"]) == 0
    report_text = capsys.readouterr().out
    assert "looked done, wasn't" in report_text and "Fv" in report_text and "Session sess" in report_text
    assert cli.main(["summary", "--results", str(tmp_path / "empty")]) == 1
    capsys.readouterr()
    cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "solo-claude"))    # a second invocation
    assert len(json.loads((session / "session.json").read_text())["invocations"]) == 2


def test_duet_hosts_are_pointed_at_a_copy_of_the_skill(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                                       capsys: pytest.CaptureFixture) -> None:
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "duet-claude-host")) == 2
    assert "SKILL.md" in capsys.readouterr().err and not fake_clients.exists()
    for name in ("SKILL.md", "scripts/duet.py", "eval/tasks/t/hidden/test_h.py"):
        write(tmp_path / "skill" / name, "x")
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "duet-claude-host")) == 0
    call = calls(fake_clients)[0]
    staged = tmp_path / "scratch" / "skill-sess"
    assert str(staged / "SKILL.md") in call["stdin"] and str(tmp_path / "skill") not in call["stdin"]
    assert f"Bash(python3 {staged / 'scripts' / 'duet.py'} *)" in call["argv"]
    assert not staged.exists()                                             # removed when the session ends
    assert "infrastructure failure" in capsys.readouterr().out             # the stand-in never ran a duet


def test_total_cost_cap_stops_the_matrix(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                         capsys: pytest.CaptureFixture) -> None:
    code = cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "solo-claude", "--seeds", "3",
                             "--max-total-cost", "0.5"))
    out = capsys.readouterr().out
    assert code == 1 and "Stopping before run 3" in out and "UNBALANCED: this session stopped early" in out
    assert len(calls(fake_clients)) == 2
    manifest = json.loads((tmp_path / "out" / "sess" / "session.json").read_text())["invocations"][0]
    assert manifest["completed"] == 2 and "--max-total-cost" in manifest["stopped_early"]


def test_run_refuses_bad_requests(task_dir: Path, tmp_path: Path, fake_clients: Path,
                                  capsys: pytest.CaptureFixture) -> None:
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "solo-gpt")) == 2
    assert "unknown arm" in capsys.readouterr().err
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "nope", "--arms", "all")) == 2
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", ",", "--arms", "all")) == 2
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "all", "--seeds", "0")) == 2
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "all", "--max-cost", "0")) == 2
    assert cli.main(cli_args(task_dir, tmp_path, "--tasks", "adder", "--arms", "all", "--duet-timeout", "0")) == 2
    with pytest.raises(SystemExit):
        cli.main(["run", "--arms", "all"])                                  # --tasks is required: no silent "everything"
    capsys.readouterr()
    (tmp_path / "project" / ".git").mkdir(parents=True)
    code = cli.main(["run", "--tasks-dir", str(task_dir.parent), "--tasks", "adder", "--arms", "solo-claude",
                     "--results", str(tmp_path / "project" / "results")])
    assert code == 2 and "inside the repository" in capsys.readouterr().err
    code = cli.main(["run", "--tasks-dir", str(task_dir.parent), "--tasks", "adder", "--arms", "solo-claude",
                     "--results", str(tmp_path / "out"), "--work-dir", str(tmp_path / "out" / "work")])
    assert code == 2 and "too close" in capsys.readouterr().err
    assert not fake_clients.exists()


def test_list_and_verify_on_the_fixture(task_dir: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["list", "--tasks-dir", str(task_dir.parent)]) == 0
    listing = capsys.readouterr().out
    assert "adder" in listing and arms.BEST_OF_TWO in listing
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
