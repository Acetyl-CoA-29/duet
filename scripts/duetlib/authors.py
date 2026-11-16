"""Who has changed the workspace since its files were last approved by everyone who had to approve them."""
from __future__ import annotations

from typing import Any, Dict, List

from . import gates
from .state import Message, State


def models(state: State) -> List[str]:
    return [state["host"], state["peer"]]


def writers(state: State) -> List[str]:
    """The models with unreviewed changes in the files. Runs from before 1.6 recorded one name only."""
    recorded = state.get("writers")
    if recorded is None:
        last = state.get("last_writer")
        return [last] if last else []
    return list(recorded)


def reviewers_for(state: State, who: List[str]) -> List[str]:
    """One writer is reviewed by the other model; two writers, or none, need both to approve."""
    return [name for name in models(state) if name not in who] if len(who) == 1 else models(state)


def _settled(state: State, messages: List[Message], who: List[str]) -> bool:
    """True when the files the runner last saw carry every approval their writers needed."""
    seen = state.get("seen_fingerprint")
    view = {**state, "last_writer": who[0] if len(who) == 1 else None}
    return bool(who) and bool(seen) and gates.finish_report(view, messages, seen)[0]


def observed(state: State, messages: List[Message], current: str, actor: str, force: bool = False) -> Dict[str, Any]:
    """State changes recording that `actor` is behind whatever differs on disk from what was last seen.

    `force` adds the actor even when the fingerprint was already recorded, for a peer write turn whose
    change another command happened to see first.
    """
    pending = state.get("pending_turn") or {}
    if pending.get("write") and actor == state["host"]:
        actor = state["peer"]
    seen = state.get("seen_fingerprint")
    moved = seen is not None and seen != current
    if not moved and not force:
        return {"seen_fingerprint": current}
    who = writers(state)
    fresh_start = not who or (moved and _settled(state, messages, who))
    return {"seen_fingerprint": current, "last_writer": actor,
            "writers": [actor] if fresh_start else sorted(set(who) | {actor})}


def effective(state: State, messages: List[Message], current: str) -> List[str]:
    """The writers as they stand now: a change no command recorded is put down to the host's side."""
    return writers({**state, **observed(state, messages, current, state["host"])})


def gate_view(state: State, messages: List[Message], current: str) -> State:
    """The state as the finish gate should read it: one writer needs the other's approval, else both."""
    who = effective(state, messages, current)
    return {**state, "seen_fingerprint": current, "last_writer": who[0] if len(who) == 1 else None}
