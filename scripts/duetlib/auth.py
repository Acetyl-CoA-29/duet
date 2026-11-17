"""How the peer's client signs in: the user's subscription plan, or an API key from the environment.

The runner never reads, stores or prints a key's value. It only decides which variable names reach
the peer's process:

- `plan`: the peer lab's key variables are withheld, so the client uses its signed-in account.
- `api-key`: a key must be present. Claude Code uses `ANTHROPIC_API_KEY` by itself in a
  non-interactive run. Codex reads `CODEX_API_KEY` for `codex exec` and ignores a bare
  `OPENAI_API_KEY`, so that key is passed on under the name Codex reads.
- `auto` (the default): the environment is passed as it is and each client decides.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional

AUTO, PLAN, API_KEY = "auto", "plan", "api-key"
MODES = (AUTO, PLAN, API_KEY)
# In the order each is preferred. The first present one is the key the client will use.
KEY_NAMES = {"codex": ("CODEX_API_KEY", "OPENAI_API_KEY"), "claude": ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")}
CODEX_READS = "CODEX_API_KEY"
LAB = {"codex": "OpenAI", "claude": "Anthropic"}


def mode_of(state: Mapping[str, object]) -> str:
    """The run's sign-in mode; runs started before this option existed behave as `auto`."""
    value = state.get("peer_auth")
    return value if value in MODES else AUTO


def key_name(peer: str, environ: Mapping[str, str]) -> Optional[str]:
    """The name of the variable holding the peer lab's key, or None. Never the value."""
    return next((name for name in KEY_NAMES[peer] if environ.get(name, "").strip()), None)


def problem(peer: str, mode: str, environ: Mapping[str, str]) -> Optional[str]:
    """Why the peer cannot sign in the way the run asks, if that is the case."""
    if mode == API_KEY and key_name(peer, environ) is None:
        names = " or ".join(KEY_NAMES[peer][::-1] if peer == "claude" else KEY_NAMES[peer])
        return (f"this run signs the {peer} peer in with an API key (--peer-auth api-key), but no {LAB[peer]} "
                f"key is set in this environment. Export {names} and run the command again")
    return None


def apply(peer: str, mode: str, env: Mapping[str, str]) -> Dict[str, str]:
    """The peer's environment with the sign-in choice applied. Returns a new mapping."""
    if mode == PLAN:
        return {name: value for name, value in env.items() if name not in KEY_NAMES[peer]}
    if mode == API_KEY and peer == "codex" and not env.get(CODEX_READS, "").strip():
        found = key_name(peer, env)
        return {**env, CODEX_READS: env[found]} if found else dict(env)
    return dict(env)


def describe(peer: str, mode: str, environ: Mapping[str, str]) -> str:
    """One line for `status`: how the peer signs in on the next turn, with no key material."""
    found = key_name(peer, environ)
    if mode == PLAN:
        return f"plan: the {peer} client's signed-in account; {LAB[peer]} API keys are withheld from it"
    if mode == API_KEY:
        return (f"api-key: {found} from the environment" if found
                else f"api-key: but no {LAB[peer]} key is set in this environment")
    if peer == "claude":
        return (f"auto: {found} is set, so the Claude client bills that key, not the subscription" if found
                else "auto: the Claude client's signed-in account")
    if found == CODEX_READS:
        return "auto: CODEX_API_KEY is set, so the Codex client bills that key, not the subscription"
    return "auto: the Codex client's saved sign-in (a bare OPENAI_API_KEY is not used by `codex exec`)"


def init_notes(peer: str, mode: str, environ: Mapping[str, str]) -> List[str]:
    """Warnings worth showing once when a run starts."""
    found = key_name(peer, environ)
    if mode != AUTO or found is None:
        return []
    if peer == "claude" or found == CODEX_READS:
        return [f"{found} is set in this environment, so the {peer} client will bill that API key instead of "
                "the subscription plan. Pass --peer-auth plan to use the plan, or --peer-auth api-key to make "
                "the key the stated choice"]
    return [f"{found} is set but `codex exec` does not use it by itself: the peer will use the Codex client's "
            "saved sign-in. Pass --peer-auth api-key to sign the peer in with that key"]
