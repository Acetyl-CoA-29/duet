"""Which models took part in a run, and how the runner knows.

The Claude client names the model in each result. The Codex client does not, so for a Codex peer
the runner can only say what was requested or what the client was configured with when the run
started. Every name is paired with its source so a report never presents a guess as a fact.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,119}$")
CODEX_HOME_ENV = "CODEX_HOME"
CODEX_CONFIG = "config.toml"
CONFIG_MODEL_RE = re.compile(r"""^model\s*=\s*(["'])([^"']+)\1\s*(#.*)?$""")
MAX_CONFIG_BYTES = 1 << 20

REPORTED = "reported by the peer's client"
REQUESTED = "requested with --peer-model; the {peer} client does not report the model it ran"
CONFIGURED = "the {peer} client's configured default when the run started; the client does not report the model it ran"
PENDING = "not known until the first peer turn"
UNKNOWN = "not known: the {peer} client reports no model and none was requested or configured"
STATED = "stated by the host with --host-model"
UNSTATED = "not stated; pass --host-model at init"


def clean(value: Any) -> Optional[str]:
    """A model name safe to store and print, or None for anything that does not look like one."""
    return value if isinstance(value, str) and MODEL_RE.match(value) else None


def reported_by_claude(data: Dict[str, Any]) -> Optional[str]:
    """The model that produced the reply: the entry in `modelUsage` with the most output tokens
    (the client may also list a small helper model)."""
    usage = data.get("modelUsage")
    if not isinstance(usage, dict):
        return None

    def weight(name: str) -> float:
        entry = usage[name]
        tokens = entry.get("outputTokens") if isinstance(entry, dict) else None
        return float(tokens) if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) else 0.0

    names = [name for name in usage if clean(name)]
    return max(names, key=weight) if names else None


def codex_configured() -> Optional[str]:
    """The top-level `model` in the Codex client's configuration, read without a TOML parser
    (Python 3.9): only lines before the first table header are considered."""
    home = os.environ.get(CODEX_HOME_ENV) or str(Path.home() / ".codex")
    try:
        path = Path(home).expanduser() / CODEX_CONFIG
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            return None
        found = CONFIG_MODEL_RE.match(stripped)
        if found:
            return clean(found.group(2))
    return None


def configured_for(peer: str) -> Optional[str]:
    return codex_configured() if peer == "codex" else None


def seen_change(reported: Optional[str]) -> Dict[str, Any]:
    """State fields recording the model a peer turn reported, when it reported one."""
    name = clean(reported)
    return {"peer_model_seen": name} if name else {}


def peer_identity(state: Dict[str, Any]) -> Dict[str, Optional[str]]:
    peer = state["peer"]
    for key, source in (("peer_model_seen", REPORTED), ("peer_model", REQUESTED),
                        ("peer_model_configured", CONFIGURED)):
        name = clean(state.get(key))
        if name:
            return {"name": name, "source": source.format(peer=peer)}
    waiting = peer == "claude" and not state.get("peer_turns")
    return {"name": None, "source": PENDING if waiting else UNKNOWN.format(peer=peer)}


def host_identity(state: Dict[str, Any]) -> Dict[str, Optional[str]]:
    name = clean(state.get("host_model"))
    return {"name": name, "source": STATED if name else UNSTATED}


def identities(state: Dict[str, Any]) -> Dict[str, Dict[str, Optional[str]]]:
    return {"host": host_identity(state), "peer": peer_identity(state)}


def line(state: Dict[str, Any]) -> str:
    """One sentence naming both models for a finish message."""
    both = identities(state)
    parts = [f"{state[role]} ran {both[role]['name'] or 'an unrecorded model'} ({both[role]['source']})"
             for role in ("host", "peer")]
    return "; ".join(parts)
