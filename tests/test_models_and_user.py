"""Which models took part is recorded with its source, and the user's words reopen review. No model is called."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from duetlib import cli, gates, models, peers, state as st  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('ready')\n", encoding="utf-8")
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "runs"))
    monkeypatch.setenv("DUET_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DUET_CLAUDE_BIN", str(ROOT / "tests" / "fake_claude.py"))
    monkeypatch.setenv("DUET_CODEX_BIN", str(ROOT / "tests" / "fake_codex.py"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(peers, "launch_problem", lambda _peer: None)
    goal = tmp_path / "goal.md"
    goal.write_text("Deliver the agreed program", encoding="utf-8")
    return {"tmp": tmp_path, "ws": workspace, "goal": goal}


def begin(home, capsys, *extra: str) -> Path:
    assert cli.main(["init", "--workspace", str(home["ws"]), "--goal-file", str(home["goal"]),
                     "--accept", "true", "--accept-unproven", "fixture", "--skip-proposals", "fixture",
                     *extra]) == cli.EXIT_OK
    return Path(capsys.readouterr().out.strip().splitlines()[-1])


def speak(home, run: Path, text: str, *extra: str) -> int:
    path = home["tmp"] / "message.md"
    path.write_text(text, encoding="utf-8")
    return cli.main(["say", "--run", str(run), "--file", str(path), *extra])


def report(run: Path, capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["status", "--run", str(run)]) == cli.EXIT_OK
    return json.loads(capsys.readouterr().out)


# --- model identity ------------------------------------------------------------------------------

def test_claude_peer_model_is_taken_from_what_the_client_reported(home, capsys, monkeypatch):
    run = begin(home, capsys, "--peer", "claude", "--host-model", "gpt-host-1")
    before = report(run, capsys)["models"]
    assert before["peer"] == {"name": None, "source": models.PENDING}
    assert before["host"] == {"name": "gpt-host-1", "source": models.STATED}
    monkeypatch.setenv("FAKE_MODEL", "claude-main-9")
    assert speak(home, run, "Let us frame it.", "--phase", "frame") == 0
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == 0
    after = report(run, capsys)["models"]["peer"]
    assert after == {"name": "claude-main-9", "source": models.REPORTED}  # the main model, not the helper


def test_reported_model_outranks_the_requested_one(home, capsys, monkeypatch):
    run = begin(home, capsys, "--peer", "claude", "--peer-model", "claude-asked-1")
    assert report(run, capsys)["models"]["peer"]["name"] == "claude-asked-1"
    monkeypatch.setenv("FAKE_MODEL", "claude-actually-ran-2")
    assert speak(home, run, "Frame.", "--phase", "frame") == 0
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == 0
    assert report(run, capsys)["models"]["peer"]["name"] == "claude-actually-ran-2"


def test_codex_peer_model_is_labelled_by_how_the_runner_knows_it(home, capsys):
    unknown = report(begin(home, capsys), capsys)["models"]
    assert unknown["peer"] == {"name": None, "source": models.UNKNOWN.format(peer="codex")}
    assert unknown["host"] == {"name": None, "source": models.UNSTATED}
    config = home["tmp"] / "codex-home"
    config.mkdir()
    (config / "config.toml").write_text('# mine\nmodel = "gpt-configured-3"  # default\n[profiles.x]\nmodel = "other"\n')
    configured = report(begin(home, capsys), capsys)["models"]["peer"]
    assert configured == {"name": "gpt-configured-3", "source": models.CONFIGURED.format(peer="codex")}
    requested = report(begin(home, capsys, "--peer-model", "gpt-asked-4"), capsys)["models"]["peer"]
    assert requested == {"name": "gpt-asked-4", "source": models.REQUESTED.format(peer="codex")}


@pytest.mark.parametrize("text", ['[profiles.x]\nmodel = "nested"\n', 'model = "bad name with spaces"\n',
                                  "model = unquoted\n", ""])
def test_codex_configuration_that_names_no_usable_default_is_ignored(home, text):
    config = home["tmp"] / "codex-home"
    config.mkdir()
    (config / "config.toml").write_text(text)
    assert models.codex_configured() is None


@pytest.mark.parametrize("flag", ["--peer-model", "--host-model"])
def test_a_value_that_is_not_a_model_name_is_refused_before_a_run_exists(home, capsys, flag):
    assert cli.main(["init", "--workspace", str(home["ws"]), "--goal-file", str(home["goal"]),
                     "--accept", "true", flag, "not a model\nname"]) == cli.EXIT_USAGE
    assert "does not look like a model name" in capsys.readouterr().err
    assert not (home["tmp"] / "runs").exists() or not any((home["tmp"] / "runs").rglob("state.json"))


@pytest.mark.parametrize("usage,expected", [
    ({"a-model": {"outputTokens": 1}, "b-model": {"outputTokens": 7}}, "b-model"),
    ({"only-model": "not a dict"}, "only-model"), ({"bad name": {}}, None), ({}, None), (None, None), ([1], None),
    ({"x-model": {"outputTokens": True}, "y-model": {"outputTokens": 0.5}}, "y-model"),
])
def test_reported_claude_model_tolerates_any_shape(usage, expected):
    assert models.reported_by_claude({"modelUsage": usage}) == expected


def test_finish_names_both_models_and_how_each_is_known(home, capsys, monkeypatch):
    run = begin(home, capsys, "--peer", "claude", "--host-model", "gpt-host-1")
    monkeypatch.setenv("FAKE_MODEL", "claude-main-9")
    assert speak(home, run, "Built it.", "--phase", "build") == 0
    assert cli.main(["check", "--run", str(run)]) == 0
    assert cli.main(["peer", "--run", str(run), "--phase", "review"]) == 0
    assert speak(home, run, "Reviewed.", "--phase", "review", "--verdict", "approve") == 0
    capsys.readouterr()
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "codex ran gpt-host-1 (stated by the host" in out and "claude ran claude-main-9 (reported by" in out


# --- the user's words ----------------------------------------------------------------------------

def test_anything_the_user_says_after_the_reviews_requires_fresh_reviews(home, capsys):
    run = begin(home, capsys, "--peer", "claude")
    assert speak(home, run, "Built it.", "--phase", "build") == 0
    assert cli.main(["check", "--run", str(run)]) == 0
    assert cli.main(["peer", "--run", str(run), "--phase", "review"]) == 0
    assert speak(home, run, "Reviewed.", "--phase", "review", "--verdict", "approve") == 0
    assert report(run, capsys)["ready_to_finish"]
    assert speak(home, run, "It must also handle empty input.", "--speaker", "user") == 0
    blocked = report(run, capsys)
    assert not blocked["ready_to_finish"]
    assert sum("later message from the user" in reason for reason in blocked["blocking"]) == 2
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert speak(home, run, "Reviewed again with that in mind.", "--phase", "review", "--verdict", "approve") == 0
    assert not report(run, capsys)["ready_to_finish"]  # the peer has not yet been sent the user's words
    assert cli.main(["peer", "--run", str(run), "--phase", "review"]) == 0
    assert report(run, capsys)["ready_to_finish"]


def test_settle_marks_only_the_users_message(home, capsys):
    run = begin(home, capsys, "--peer", "claude")
    assert speak(home, run, "Mine.", "--phase", "plan", "--settle") == cli.EXIT_USAGE
    assert "--speaker user" in capsys.readouterr().err
    assert speak(home, run, "Go with the simpler plan.", "--speaker", "user", "--settle") == 0
    last = st.read_messages(run)[-1]
    assert last["speaker"] == "user" and last["meta"].get("settles") is True
