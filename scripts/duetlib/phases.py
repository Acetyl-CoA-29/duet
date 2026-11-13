"""The run's phase sequence: frame -> propose -> plan -> build <-> review, and what may follow what."""
from __future__ import annotations

from typing import Dict, List, Optional

from .state import PHASES, Message, State

FRAME, PROPOSE, PLAN, BUILD, REVIEW = PHASES
ALIASES: Dict[str, str] = {"debate": PLAN}  # older runs and habits; critique now happens in plan
RETIRED: Dict[str, str] = {
    "test": "`test` is no longer a phase: run `check`, which records the acceptance output for both "
            "models, and discuss the result in build or review",
}
LEGACY_LABELS: Dict[str, str] = {**ALIASES, "test": BUILD}  # labels found in runs recorded before 1.5
REPLAN_FROM = (BUILD, REVIEW)
REPLAN_KIND = "replan"


def normalise(name: str) -> str:
    """Map a phase name given on the command line to a current one; ValueError if it cannot be used."""
    lowered = name.strip().lower()
    if lowered in RETIRED:
        raise ValueError(RETIRED[lowered])
    resolved = ALIASES.get(lowered, lowered)
    if resolved not in PHASES:
        raise ValueError(f"unknown phase '{name}'; choose one of {', '.join(PHASES)}")
    return resolved


def _label(message: Message) -> str:
    return LEGACY_LABELS.get(message["phase"], message["phase"])


def _model_messages(state: State, messages: List[Message]) -> List[Message]:
    models = (state["host"], state["peer"])
    return [m for m in messages if m["kind"] == "message" and m["speaker"] in models]


def current(state: State, messages: List[Message]) -> str:
    """The phase the run is in. Runs recorded before phases were tracked are read from their messages."""
    recorded = state.get("phase")
    if recorded in PHASES:
        return recorded
    said = [_label(m) for m in _model_messages(state, messages)]
    return next((label for label in reversed(said) if label in PHASES), FRAME)


def _both_spoke(state: State, messages: List[Message], phase: str, after_seq: int = 0) -> bool:
    speakers = {m["speaker"] for m in _model_messages(state, messages)
                if _label(m) == phase and m["seq"] > after_seq}
    return {state["host"], state["peer"]} <= speakers


def _last_replan(messages: List[Message]) -> int:
    return max((m["seq"] for m in messages if m["kind"] == REPLAN_KIND), default=0)


def _forward_refusal(state: State, messages: List[Message], here: str, target: str) -> Optional[str]:
    """Why the run may not step forward from `here` to `target` (None if it may)."""
    skip = bool(state.get("skip_proposals"))
    if target == PLAN:
        if skip or _both_spoke(state, messages, PROPOSE):
            return None
        return "plan needs both blind proposals first: run the propose phase for both models"
    if target == BUILD and here in (FRAME, PROPOSE) and not skip:
        return ("no build before the blind proposal round and a joint plan (or the run must have been "
                "started with --skip-proposals)")
    if target == BUILD and here == PLAN and not skip:
        if _both_spoke(state, messages, PLAN, _last_replan(messages)):
            return None
        return "build opens once both models have spoken in plan: take a plan turn each first"
    if target == REVIEW and here != BUILD:
        return "review follows build: record or run a build turn first"
    return None


def refusal(state: State, messages: List[Message], target: str, replan: Optional[str] = None) -> Optional[str]:
    """Why a command may not act in `target` from the current phase; None when it may."""
    here = current(state, messages)
    if target == here:
        return None
    if here in REPLAN_FROM and target == PLAN:
        return None if replan else (f"going back to plan from {here} must be explicit: "
                                    'add --replan "<what invalidated the plan>"')
    if PHASES.index(target) < PHASES.index(here) and not (here == REVIEW and target == BUILD):
        return f"the run is in {here}; it cannot go back to {target}"
    problem = _forward_refusal(state, messages, here, target)
    return f"the run is in {here}: {problem}" if problem else None


def allowed_next(state: State, messages: List[Message]) -> List[str]:
    """Phases a `say` or `peer` command may name right now without --replan."""
    return [phase for phase in PHASES if refusal(state, messages, phase) is None]


def needs_replan(state: State, messages: List[Message]) -> List[str]:
    return [PLAN] if current(state, messages) in REPLAN_FROM else []
