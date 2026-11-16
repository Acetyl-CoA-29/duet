"""Regression tests for completion races and stale review evidence. No model is called."""
from __future__ import annotations

import sys
import signal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from duetlib import authors, cli, gates, peers, state as st, turn  # noqa: E402


@pytest.fixture
def ready_run(tmp_path, monkeypatch):
    """A fully checked and approved run, without invoking either external client."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('ready')\n", encoding="utf-8")
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "runs"))
    monkeypatch.setenv("DUET_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setattr(peers, "launch_problem", lambda _peer: None)
    fingerprint = gates.fingerprint(workspace)
    run = st.new_run(workspace, "Deliver the agreed program", "codex", "true", None, 30, None,
                     skip_proposals="fixture", seen_fingerprint=fingerprint)
    st.update_state(run, lambda _fresh: {"phase": "build"})
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_OK
    for speaker in ("claude", "codex"):
        st.append_message(run, speaker, "review", "Reviewed the current files and check.\nVERDICT: APPROVE",
                          meta={"verdict": "approve", "fingerprint": fingerprint})
    st.update_state(run, lambda _fresh: {"phase": "review"})
    assert cli._report(run)["ready_to_finish"]
    return run, workspace


@pytest.mark.parametrize("command", ["check", "experiment"])
def test_finish_refuses_an_admitted_command_until_its_receipt(ready_run, monkeypatch, command):
    run, workspace = ready_run
    finish_codes = []

    def still_running(_command, _workspace, _timeout):
        # Simulates another CLI process while this admitted command is outside the state lock.
        finish_codes.append(cli.main(["finish", "--run", str(run)]))
        (workspace / "app.py").write_text("print('changed during command')\n", encoding="utf-8")
        return 0, "completed"

    monkeypatch.setattr(gates, "run_command", still_running)
    argv = [command, "--run", str(run)] + (["--cmd", "probe"] if command == "experiment" else [])
    result = cli.main(argv)
    assert finish_codes == [cli.EXIT_NOT_DONE]
    assert result == cli.EXIT_OK
    assert st.load_state(run)["status"] == "active"
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE


def test_peer_review_cannot_approve_a_check_it_was_never_sent(ready_run, monkeypatch):
    run, _workspace = ready_run

    def review_before_check(*_args, **_kwargs):
        assert cli.main(["check", "--run", str(run)]) == cli.EXIT_OK
        return peers.PeerResult(True, "VERDICT: APPROVE", "review-session")

    monkeypatch.setattr(peers, "run_turn", review_before_check)
    assert cli.main(["peer", "--run", str(run), "--phase", "review"]) == cli.EXIT_OK
    fingerprint = gates.fingerprint(Path(st.load_state(run)["workspace"]))
    st.append_message(run, "claude", "review", "Reviewed the new check.\nVERDICT: APPROVE",
                      meta={"verdict": "approve", "fingerprint": fingerprint})
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    blocking = " ".join(cli._report(run)["blocking"])
    assert "codex" in blocking and ("check" in blocking or "acceptance" in blocking)


def test_authorship_does_not_reset_from_reviews_invalidated_by_a_new_check():
    state = {"host": "claude", "peer": "codex", "seen_fingerprint": "old", "writers": ["claude", "codex"],
             "last_writer": "codex", "accept_cmd": "true", "last_check": {"seq": 4, "passed": False},
             "accept_waived": None}
    reviews = [{"seq": seq, "speaker": speaker, "phase": "review", "kind": "message",
                "meta": {"verdict": "approve", "fingerprint": "old"}}
               for seq, speaker in enumerate(("claude", "codex"), 2)]
    observed = authors.observed(state, reviews, "new", "claude")
    assert observed["writers"] == ["claude", "codex"]


def test_changing_to_waived_acceptance_needs_review_of_the_new_evidence(ready_run):
    run, _workspace = ready_run
    assert cli.main(["accept", "--run", str(run), "--waive", "Judge the documented examples",
                     "--by-user", "Use the manual criteria instead"]) == cli.EXIT_OK
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE


@pytest.mark.parametrize("waived", [False, True])
def test_replan_cannot_reuse_review_approvals_from_the_old_plan(ready_run, waived):
    run, _workspace = ready_run
    if waived:
        st.update_state(run, lambda _fresh: {"accept_cmd": None, "accept_waived": "manual criteria"})
    draft = run / "drafts" / "replan.md"
    draft.write_text("Reconsider the operating constraints.\n", encoding="utf-8")
    assert cli.main(["say", "--run", str(run), "--phase", "plan", "--replan", "new constraints",
                     "--file", str(draft), "--verdict", "approve"]) == cli.EXIT_OK
    st.append_message(run, "codex", "plan", "The revised plan is agreed.\nVERDICT: APPROVE",
                      meta={"verdict": "approve"})
    assert cli.main(["say", "--run", str(run), "--phase", "build", "--file", str(draft)]) == cli.EXIT_OK
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE


def test_second_command_is_refused_without_overwriting_output(ready_run, monkeypatch):
    run, _workspace = ready_run

    def running_command(*_args):
        assert cli.main(["check", "--run", str(run)]) == cli.EXIT_USAGE
        assert cli.main(["experiment", "--run", str(run), "--cmd", "true"]) == cli.EXIT_USAGE
        return 0, "unique second check output"

    monkeypatch.setattr(gates, "run_command", running_command)
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_OK
    assert len(list((run / "checks").glob("*.log"))) == 2
    assert (run / "checks" / "002.log").read_text() == "unique second check output"


@pytest.mark.parametrize("kind", ["check", "experiment"])
def test_abandoned_command_invalidates_evidence_and_recovers_once(ready_run, kind):
    run, _workspace = ready_run
    st.update_state(run, lambda _state: {"pending_command": {"kind": kind, "started": "interrupted"}})
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert not st.load_state(run).get("pending_command")
    if kind == "check":
        assert st.load_state(run)["last_check"] is None
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert len([m for m in st.read_messages(run) if m["kind"] == "command_error"]) == 1


def test_late_peer_reply_cannot_rewind_the_phase_and_unlock_acceptance(ready_run, monkeypatch):
    run, _workspace = ready_run
    st.update_state(run, lambda _state: {"phase": "frame"})
    draft = run / "drafts" / "proposal.md"
    draft.write_text("Independent host proposal.")

    def slow_frame(*_args, **_kwargs):
        assert cli.main(["say", "--run", str(run), "--phase", "propose", "--file", str(draft)]) == cli.EXIT_OK
        return peers.PeerResult(True, "Frame complete.", "late-frame-session")

    monkeypatch.setattr(peers, "run_turn", slow_frame)
    assert cli.main(["peer", "--run", str(run), "--phase", "frame"]) == cli.EXIT_OK
    assert st.load_state(run)["phase"] == "propose"
    assert cli.main(["accept", "--run", str(run), "--cmd", "false"]) == cli.EXIT_USAGE


def test_source_changed_by_acceptance_is_attributed_to_the_host(ready_run, monkeypatch):
    run, workspace = ready_run
    st.update_state(run, lambda _state: {"writers": ["codex"], "last_writer": "codex"})

    def formatter(*_args):
        (workspace / "app.py").write_text("print('check changed source')\n")
        return 0, "formatted"

    monkeypatch.setattr(gates, "run_command", formatter)
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_OK
    assert st.load_state(run)["writers"] == ["claude"]
    assert not st.load_state(run)["last_check"]["stable"]
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_OK
    current = gates.fingerprint(workspace)
    st.append_message(run, "claude", "review", "Approved", meta={"verdict": "approve", "fingerprint": current})
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE


@pytest.mark.parametrize("args", [
    ["accept", "--cmd", "false", "--by-user", "   "],
    ["extend", "--turns", "5", "--reason", " \t"],
    ["reseal", "--by-user", "\n"],
    ["experiment", "--cmd", "   "],
])
def test_empty_authorization_or_command_does_not_mutate_run(ready_run, args):
    run, _workspace = ready_run
    before = (run / st.STATE_FILE).read_bytes(), (run / st.MESSAGES_FILE).read_bytes()
    assert cli.main([*args, "--run", str(run)]) == cli.EXIT_USAGE
    assert ((run / st.STATE_FILE).read_bytes(), (run / st.MESSAGES_FILE).read_bytes()) == before


@pytest.mark.parametrize("command", ["check", "experiment"])
def test_stopped_command_is_recorded_once_and_releases_lock(ready_run, monkeypatch, command):
    run, _workspace = ready_run
    previous_check = st.load_state(run)["last_check"]

    def stopped(*_args):
        raise turn.Stopped(signal.SIGTERM)

    with monkeypatch.context() as patch:
        patch.setattr(gates, "run_command", stopped)
        argv = [command, "--run", str(run)] + (["--cmd", "true"] if command == "experiment" else [])
        assert cli.main(argv) == cli.EXIT_CHECK_FAILED
    state = st.load_state(run)
    assert state["pending_command"] is None
    assert state["last_check"] == (None if command == "check" else previous_check)
    assert len([m for m in st.read_messages(run) if m["kind"] == "command_error"]) == 1
    assert cli.main(["finish", "--run", str(run)]) == cli.EXIT_NOT_DONE
    assert cli.main(["check", "--run", str(run)]) == cli.EXIT_OK
