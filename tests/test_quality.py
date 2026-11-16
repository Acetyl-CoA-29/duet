"""The runner's own quality checks: computed flags that block finish, and proof that acceptance can fail."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from duetlib import cli, facts, peers, state as st  # noqa: E402

FAILS_UNTIL_DONE = "test -f done.txt"


@pytest.fixture
def home(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "app.py").write_text("def greet(name):\n    # TODO: old note that was here before the run\n"
                                      "    return 'hi ' + name\n", encoding="utf-8")
    (workspace / "tests" / "test_app.py").write_text(
        "from app import greet\n\n\ndef test_greets():\n    assert greet('a') == 'hi a'\n    assert greet('') == 'hi '\n\n\n"
        "def test_type():\n    assert isinstance(greet('a'), str)\n", encoding="utf-8")
    log = tmp_path / "peer.log"
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "runs"))
    monkeypatch.setenv("DUET_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DUET_CODEX_BIN", str(ROOT / "tests" / "fake_codex.py"))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(peers, "launch_problem", lambda _peer: None)
    goal = tmp_path / "goal.md"
    goal.write_text("Deliver the agreed program", encoding="utf-8")
    return {"tmp": tmp_path, "ws": workspace, "goal": goal, "log": log}


def begin(home, capsys, *extra: str) -> Path:
    acceptance = [] if any(a.startswith("--accept") or a == "--waive-accept" for a in extra) else [
        "--accept", "true", "--accept-unproven", "fixture"]
    assert cli.main(["init", "--workspace", str(home["ws"]), "--goal-file", str(home["goal"]),
                     "--skip-proposals", "fixture", *acceptance, *extra]) == cli.EXIT_OK
    return Path(capsys.readouterr().out.strip().splitlines()[-1])


def speak(home, run: Path, text: str, *extra: str) -> int:
    path = home["tmp"] / "message.md"
    path.write_text(text, encoding="utf-8")
    return cli.main(["say", "--run", str(run), "--file", str(path), *extra])


def report(run: Path, capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["status", "--run", str(run)]) == cli.EXIT_OK
    return json.loads(capsys.readouterr().out)


def flags(run: Path, capsys) -> list:
    return [flag["id"] for flag in report(run, capsys)["quality_flags"]]


def approve_both(home, run: Path) -> None:
    assert cli.main(["check", "--run", str(run)]) == 0
    assert cli.main(["peer", "--run", str(run), "--phase", "review"]) == 0
    assert speak(home, run, "Reviewed.", "--phase", "review", "--verdict", "approve") == 0


def last_prompt(home) -> str:
    return json.loads(home["log"].read_text().strip().splitlines()[-1])["stdin"]


# --- facts about one file ------------------------------------------------------------------------

def test_python_facts_count_stubs_empty_bodies_dropped_errors_and_tests():
    text = (
        "import abc\n\n"
        "def real():\n    return 1\n\n"
        "def empty():\n    pass\n\n"
        "def documented_only():\n    \"\"\"Later.\"\"\"\n\n"
        "class Base(abc.ABC):\n    @abc.abstractmethod\n    def must(self):\n        ...\n\n"
        "def swallow():\n    try:\n        real()\n    except Exception:\n        pass\n"
        "    try:\n        real()\n    except:\n        return None\n"
        "    try:\n        real()\n    except ValueError:\n        pass\n"       # narrow: a decision, not a mask
        "    try:\n        real()\n    except Exception as exc:\n        raise RuntimeError('x') from exc\n\n"
        "def test_one():\n    assert real() == 1\n    # FIXME tighten\n"
    )
    found = facts.file_facts("pkg/test_thing.py", text)
    assert found["empty"] == ["documented_only", "empty"]
    assert found["silent"] == 2 and found["tests"] == ["test_one"] and found["asserts"] == 1
    assert found["stubs"] == 1 and found["test_file"] is True


def test_other_languages_and_unparsable_python_fall_back_to_patterns():
    js = "it('adds', () => { expect(add(1, 2)).toBe(3) })\nit.skip('later', () => {})\ntry { go() } catch (e) {}\n"
    found = facts.file_facts("src/add.test.ts", js)
    assert found["tests"] == ["adds"] and found["asserts"] == 1 and found["skips"] == 1 and found["silent"] == 1
    broken = facts.file_facts("bad.py", "def broken(:\n    # TODO finish\n")
    assert broken["stubs"] == 1 and broken["empty"] == []
    assert facts.file_facts("notes.md", "TODO write docs")["stubs"] == 0  # prose is not code


@pytest.mark.parametrize("name,expected", [("tests/test_a.py", True), ("pkg/a_test.go", True), ("src/a.spec.ts", True),
                                           ("src/__tests__/a.js", True), ("src/contest.py", False), ("app.py", False)])
def test_test_files_are_recognised_by_name(name, expected):
    assert facts.is_test_file(name) is expected


def test_credential_shaped_strings_are_counted_without_storing_them():
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    assert facts.file_facts("config.yaml", f"aws_key: {key}\n")["secrets"] == 1
    assert facts.file_facts("config.yaml", "aws_key: ${AWS_KEY}\n")["secrets"] == 0


# --- flags on a run ------------------------------------------------------------------------------

def test_what_was_already_there_is_not_flagged(home, capsys):
    run = begin(home, capsys)
    found = report(run, capsys)
    assert found["quality_checked"] is True and found["quality_flags"] == []
    (home["ws"] / "app.py").write_text("def greet(name):\n    # TODO: old note that was here before the run\n"
                                       "    return 'hello ' + name\n")
    assert flags(run, capsys) == []  # the marker count did not rise


def test_new_stub_blocks_finish_until_fixed(home, capsys):
    run = begin(home, capsys)
    (home["ws"] / "extra.py").write_text("def later():\n    raise NotImplementedError  # TODO\n\n\ndef nothing():\n    pass\n")
    assert speak(home, run, "Built.", "--phase", "build") == 0
    assert set(flags(run, capsys)) == {"stub:extra.py", "empty:extra.py"}
    approve_both(home, run)
    prompt = last_prompt(home)
    assert "FLAG stub:extra.py" in prompt and "NOT justified; finish is blocked" in prompt
    assert "Answer every FLAG in your review" in prompt and "source files changed: extra.py" in prompt
    assert "source files changed but no test file did" in prompt
    capsys.readouterr()
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert "stub:extra.py" in capsys.readouterr().out
    (home["ws"] / "extra.py").write_text("def later():\n    return 1\n\n\ndef nothing():\n    return None\n")
    assert speak(home, run, "Finished them.", "--phase", "build") == 0
    approve_both(home, run)
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_OK


def test_justification_is_recorded_needs_fresh_reviews_and_lapses_when_the_flag_grows(home, capsys):
    run = begin(home, capsys)
    (home["ws"] / "extra.py").write_text("def hook():\n    pass\n")
    assert speak(home, run, "Built.", "--phase", "build") == 0
    approve_both(home, run)
    assert cli.main(["justify", "--run", str(run), "--flag", "empty:nope.py", "--reason", "x"]) == cli.EXIT_USAGE
    assert "raised now: empty:extra.py" in capsys.readouterr().err
    assert cli.main(["justify", "--run", str(run), "--flag", "empty:extra.py",
                     "--reason", "an extension point callers override"]) == cli.EXIT_OK
    after = report(run, capsys)
    assert after["quality_flags"] == [] and not after["ready_to_finish"]  # justified, but reviews are now stale
    approve_both(home, run)
    assert "justified on the record: an extension point callers override" in last_prompt(home)
    assert report(run, capsys)["ready_to_finish"]
    (home["ws"] / "extra.py").write_text("def hook():\n    pass\n\n\ndef another():\n    pass\n")
    assert flags(run, capsys) == ["empty:extra.py"]  # a different finding than the one justified


def test_weakened_tests_are_flagged(home, capsys):
    run = begin(home, capsys)
    test_file = home["ws"] / "tests" / "test_app.py"
    test_file.write_text("import pytest\nfrom app import greet\n\n\n@pytest.mark.skip\ndef test_greets():\n"
                         "    assert greet('a') == 'hi a'\n")
    assert set(flags(run, capsys)) == {"skipped-test:tests/test_app.py", "removed-test:tests/test_app.py",
                                       "fewer-asserts:tests/test_app.py"}
    test_file.unlink()
    found = report(run, capsys)["quality_flags"]
    assert [(f["id"], f["detail"]) for f in found] == [("removed-test:tests/test_app.py", "test file deleted (2 tests)")]


def test_dropped_error_and_pasted_key_are_flagged(home, capsys):
    run = begin(home, capsys)
    (home["ws"] / "app.py").write_text("def greet(name):\n    # TODO: old note that was here before the run\n"
                                       "    try:\n        return 'hi ' + name\n    except Exception:\n        return None\n")
    (home["ws"] / "settings.toml").write_text('token = "ghp_' + "a" * 36 + '"\n')
    assert set(flags(run, capsys)) == {"silent-catch:app.py", "secret:settings.toml"}


def test_missing_or_altered_baseline_blocks_finish_instead_of_switching_the_checks_off(home, capsys):
    run = begin(home, capsys)
    assert speak(home, run, "Built.", "--phase", "build") == 0
    approve_both(home, run)
    assert report(run, capsys)["ready_to_finish"]
    baseline = run / facts.BASELINE_FILE
    baseline.write_text(baseline.read_text().replace('"stubs": 1', '"stubs": 99'))
    blocked = report(run, capsys)
    assert blocked["quality_checked"] is False and not blocked["ready_to_finish"]
    assert any("quality baseline" in reason for reason in blocked["blocking"])


def test_runs_started_before_the_checks_existed_are_not_held_to_them(home, capsys):
    run = st.new_run(home["ws"], "Old run", "codex", "true", None, 30, None, skip_proposals="fixture")
    found = report(run, capsys)
    assert found["quality_checked"] is False and found["acceptance_proven"] is True
    assert not any("quality baseline" in reason or "seen to fail" in reason for reason in found["blocking"])


# --- proof that acceptance can fail --------------------------------------------------------------

def test_run_stays_in_frame_until_acceptance_is_seen_to_fail(home, capsys):
    run = begin(home, capsys, "--accept", FAILS_UNTIL_DONE)
    start = report(run, capsys)
    assert start["allowed_next"] == ["frame"] and start["acceptance_proven"] is False
    assert speak(home, run, "Building.", "--phase", "build") == cli.EXIT_ORDER
    assert "never been seen to fail" in capsys.readouterr().err
    assert cli.main(["peer", "--run", str(run), "--phase", "plan"]) == cli.EXIT_ORDER
    assert speak(home, run, "Framing still.", "--phase", "frame") == 0
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_CHECK_FAILED  # the unmet goal fails: that is the proof
    proven = report(run, capsys)
    assert proven["acceptance_proven"] is True and "build" in proven["allowed_next"]
    assert speak(home, run, "Building.", "--phase", "build") == 0


def test_probe_proves_a_green_baseline_on_a_scratch_copy_only(home, capsys):
    run = begin(home, capsys, "--accept", "python3 -c \"import app; assert app.greet('a') == 'hi a'\"")
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_OK  # green from the start: proves nothing
    assert report(run, capsys)["acceptance_proven"] is False
    before = (home["ws"] / "app.py").read_text()
    assert cli.main(["probe", "--run", str(run), "--break", "echo 'def greet(name): return name' > app.py"]) == cli.EXIT_OK
    assert "FAILED as it should" in capsys.readouterr().out
    assert (home["ws"] / "app.py").read_text() == before and sorted(p.name for p in home["ws"].iterdir()) == ["app.py", "tests"]
    assert report(run, capsys)["acceptance_proven"] is True
    assert st.read_messages(run)[-1]["kind"] == "probe"


def test_probe_that_acceptance_survives_proves_nothing(home, capsys):
    run = begin(home, capsys, "--accept", "true")
    assert cli.main(["probe", "--run", str(run), "--break", "rm app.py"]) == cli.EXIT_CHECK_FAILED
    assert "STILL PASSED" in capsys.readouterr().out
    assert cli.main(["probe", "--run", str(run), "--break", "exit 3"]) == cli.EXIT_CHECK_FAILED
    assert "break command itself failed" in capsys.readouterr().err
    assert report(run, capsys)["acceptance_proven"] is False and (home["ws"] / "app.py").is_file()


@pytest.mark.parametrize("extra", [[], ["--break", "rm app.py", "--by-user", "fine"]])
def test_probe_takes_exactly_one_kind_of_proof(home, capsys, extra):
    run = begin(home, capsys, "--accept", "true")
    assert cli.main(["probe", "--run", str(run), *extra]) == cli.EXIT_USAGE
    assert "exactly one of" in capsys.readouterr().err


def test_user_may_go_without_proof_and_a_new_command_owes_it_again(home, capsys):
    run = begin(home, capsys, "--accept", "true")
    assert cli.main(["probe", "--run", str(run), "--by-user", "this command is fine as it is"]) == cli.EXIT_OK
    assert "without proof that it can fail: this command is fine as it is" in st.read_messages(run)[-1]["text"]
    assert speak(home, run, "Built.", "--phase", "build") == 0
    approve_both(home, run)
    assert report(run, capsys)["ready_to_finish"]
    assert cli.main(["accept", "--run", str(run), "--cmd", "test -f app.py", "--by-user", "use this instead"]) == 0
    changed = report(run, capsys)
    assert changed["acceptance_proven"] is False
    assert any("never been seen to fail" in reason for reason in changed["blocking"])


def test_waived_acceptance_owes_no_proof(home, capsys):
    run = begin(home, capsys, "--waive-accept", "prose only")
    found = report(run, capsys)
    assert found["acceptance_proven"] is True and "plan" in found["allowed_next"]


def test_probe_and_justify_refuse_a_finished_run_and_a_run_without_a_command(home, capsys):
    waived = begin(home, capsys, "--waive-accept", "prose only")
    assert cli.main(["probe", "--run", str(waived), "--break", "rm app.py"]) == cli.EXIT_USAGE
    assert "nothing to prove" in capsys.readouterr().err
