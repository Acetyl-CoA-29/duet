"""How the peer's client signs in: plan, API key, or whatever the client picks. No model is called."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from duetlib import auth, cli, peers  # noqa: E402

KEYS = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
SECRET = "value-that-must-never-be-printed"


@pytest.fixture
def home(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('ready')\n", encoding="utf-8")
    log = tmp_path / "peer.log"
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "runs"))
    monkeypatch.setenv("DUET_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DUET_CODEX_BIN", str(ROOT / "tests" / "fake_codex.py"))
    monkeypatch.setenv("DUET_CLAUDE_BIN", str(ROOT / "tests" / "fake_claude.py"))
    monkeypatch.setenv("FAKE_LOG", str(log))
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(peers, "launch_problem", lambda _peer: None)
    goal = tmp_path / "goal.md"
    goal.write_text("Deliver the agreed program", encoding="utf-8")
    return {"tmp": tmp_path, "ws": workspace, "goal": goal, "log": log}


def init(home, *extra: str) -> int:
    return cli.main(["init", "--workspace", str(home["ws"]), "--goal-file", str(home["goal"]), "--accept", "true",
                     "--accept-unproven", "fixture", "--skip-proposals", "fixture", *extra])


def begin(home, capsys, *extra: str) -> Path:
    assert init(home, *extra) == cli.EXIT_OK
    return Path(capsys.readouterr().out.strip().splitlines()[-1])


def frame_turn(home, run: Path) -> int:
    path = home["tmp"] / "message.md"
    path.write_text("Let us frame it.", encoding="utf-8")
    assert cli.main(["say", "--run", str(run), "--file", str(path), "--phase", "frame"]) == 0
    return cli.main(["peer", "--run", str(run), "--phase", "frame"])


def peer_saw(home) -> list:
    return json.loads(home["log"].read_text().strip().splitlines()[-1])["secrets"]


def sign_in(run: Path, capsys) -> str:
    capsys.readouterr()
    assert cli.main(["status", "--run", str(run)]) == 0
    return json.loads(capsys.readouterr().out)["peer_sign_in"]


# --- the rule itself -----------------------------------------------------------------------------

def test_plan_withholds_only_the_peer_labs_keys():
    env = {"OPENAI_API_KEY": "a", "CODEX_API_KEY": "b", "OPENAI_BASE_URL": "u", "PATH": "/bin"}
    assert auth.apply("codex", auth.PLAN, env) == {"OPENAI_BASE_URL": "u", "PATH": "/bin"}
    assert env["OPENAI_API_KEY"] == "a"  # the caller's mapping is not changed
    claude = {"ANTHROPIC_API_KEY": "a", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "u"}
    assert auth.apply("claude", auth.PLAN, claude) == {"ANTHROPIC_BASE_URL": "u"}


def test_api_key_mode_hands_codex_the_name_it_reads():
    assert auth.apply("codex", auth.API_KEY, {"OPENAI_API_KEY": "a"}) == {"OPENAI_API_KEY": "a", "CODEX_API_KEY": "a"}
    kept = {"OPENAI_API_KEY": "a", "CODEX_API_KEY": "b"}
    assert auth.apply("codex", auth.API_KEY, kept) == kept  # an explicit Codex key is never overwritten
    assert auth.apply("claude", auth.API_KEY, {"ANTHROPIC_API_KEY": "a"}) == {"ANTHROPIC_API_KEY": "a"}
    assert auth.apply("codex", auth.AUTO, {"OPENAI_API_KEY": "a"}) == {"OPENAI_API_KEY": "a"}


@pytest.mark.parametrize("peer,environ,missing", [
    ("codex", {}, True), ("codex", {"OPENAI_API_KEY": "  "}, True), ("codex", {"OPENAI_API_KEY": "a"}, False),
    ("codex", {"CODEX_API_KEY": "a"}, False), ("claude", {"OPENAI_API_KEY": "a"}, True),
    ("claude", {"ANTHROPIC_API_KEY": "a"}, False), ("claude", {"ANTHROPIC_AUTH_TOKEN": "a"}, False),
])
def test_api_key_mode_needs_the_peer_labs_key(peer, environ, missing):
    assert (auth.problem(peer, auth.API_KEY, environ) is not None) is missing
    assert auth.problem(peer, auth.PLAN, environ) is None and auth.problem(peer, auth.AUTO, environ) is None


def test_runs_from_before_the_option_behave_as_auto():
    assert auth.mode_of({}) == auth.AUTO and auth.mode_of({"peer_auth": "nonsense"}) == auth.AUTO
    assert auth.mode_of({"peer_auth": "plan"}) == auth.PLAN


def test_descriptions_and_notes_never_contain_a_key(monkeypatch):
    environ = {name: SECRET for name in KEYS}
    for peer in ("codex", "claude"):
        for mode in auth.MODES:
            text = auth.describe(peer, mode, environ) + " ".join(auth.init_notes(peer, mode, environ))
            assert SECRET not in text
        assert SECRET not in (auth.problem(peer, auth.API_KEY, {}) or "")


# --- on a run ------------------------------------------------------------------------------------

def test_codex_peer_with_api_key_gets_the_key_under_codex_name(home, capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    run = begin(home, capsys, "--peer-auth", "api-key")
    assert sign_in(run, capsys) == "api-key: OPENAI_API_KEY from the environment"
    assert frame_turn(home, run) == 0
    assert {"CODEX_API_KEY", "OPENAI_API_KEY"} <= set(peer_saw(home))
    for path in run.rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(errors="replace"), path  # the key is never written to the run


def test_plan_mode_keeps_the_keys_away_from_the_peer(home, capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("CODEX_API_KEY", SECRET)
    run = begin(home, capsys, "--peer-auth", "plan")
    assert "API keys are withheld" in sign_in(run, capsys)
    assert frame_turn(home, run) == 0
    assert not {"CODEX_API_KEY", "OPENAI_API_KEY"} & set(peer_saw(home))


def test_claude_peer_modes(home, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    keyed = begin(home, capsys, "--peer", "claude", "--peer-auth", "api-key")
    assert frame_turn(home, keyed) == 0 and "ANTHROPIC_API_KEY" in peer_saw(home)
    planned = begin(home, capsys, "--peer", "claude", "--peer-auth", "plan")
    assert frame_turn(home, planned) == 0 and "ANTHROPIC_API_KEY" not in peer_saw(home)
    automatic = begin(home, capsys, "--peer", "claude")
    assert "bills that key, not the subscription" in sign_in(automatic, capsys)


def test_api_key_mode_without_a_key_is_refused_at_init_and_before_a_turn(home, capsys, monkeypatch):
    assert init(home, "--peer-auth", "api-key") == cli.EXIT_USAGE
    assert "no OpenAI key is set" in capsys.readouterr().err
    assert not any((home["tmp"] / "runs").rglob("state.json")) if (home["tmp"] / "runs").exists() else True
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    run = begin(home, capsys, "--peer-auth", "api-key")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert frame_turn(home, run) == cli.EXIT_USAGE  # refused before a turn is spent
    assert "Export CODEX_API_KEY or OPENAI_API_KEY" in capsys.readouterr().err
    assert "but no OpenAI key is set" in sign_in(run, capsys)


def test_unknown_mode_is_a_usage_error(home, capsys):
    assert init(home, "--peer-auth", "password") == cli.EXIT_USAGE
    assert "--peer-auth" in capsys.readouterr().err
