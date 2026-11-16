"""Review regressions for host/peer instructions; no external clients are started."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from duetlib import prompts  # noqa: E402


@pytest.mark.parametrize("peer,host", [("codex", "claude"), ("claude", "codex")])
@pytest.mark.parametrize("first", [True, False])
@pytest.mark.parametrize("phase", ["build", "review"])
def test_engineering_standard_survives_direction_and_session_resume(peer, host, first, phase):
    state = {"peer": peer, "host": host, "accept_cmd": "python3 verify.py", "nonce": "ab12"}
    prompt = prompts.build_prompt(state, "Implement the agreed prototype.", phase, [], phase == "build", first)
    for phrase in ("safe defaults", "boundaries", "diagnos", "hard", "caps", "stop incomplete",
                   "prototype", "scope", "defects", "Claim only what you verified"):
        assert phrase.lower() in prompt.lower(), phrase
    assert "secrets" in prompt
    assert "migrat" in prompt
    assert "Turns and effort are not the scarce thing" not in prompt
    if first:
        assert "You are " + peer.capitalize() in prompt
        assert "with " + host.capitalize() in prompt


@pytest.mark.parametrize("peer,host", [("codex", "claude"), ("claude", "codex")])
@pytest.mark.parametrize("first", [True, False])
def test_readonly_prompt_does_not_promise_restoration(peer, host, first):
    state = {"peer": peer, "host": host, "accept_cmd": "python3 verify.py", "nonce": "ab12"}
    prompt = prompts.build_prompt(state, "Fix the parser.", "review", [], False, first,
                                  plan_mode=peer == "claude")
    assert "change is detected and voids your verdict" in prompt
    assert "it may not be undone for you" in prompt
    assert "snapshot" not in prompt  # runner internals are not the peer's concern
    assert "change is undone" not in prompt
    assert "a check completed later needs a new review turn" in prompt


def test_host_standard_covers_the_peer_safety_and_scope_contract():
    skill = " ".join((ROOT / "SKILL.md").read_text(encoding="utf-8").split())
    for phrase in ("safe defaults", "no secrets in code", "diagnosable failures",
                   "compatibility preserved or migrated deliberately", "Validate at boundaries",
                   "Claim only what you verified", "hard turn, cost and time caps", "stop incomplete",
                   "draft or prototype narrows scope, never hides defects"):
        assert phrase in skill
    assert "Turns and effort are not the scarce thing" not in skill
