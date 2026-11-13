"""Read cost, tokens and the final message out of what each client prints."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional

TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens", "output_tokens", "reasoning_output_tokens")


@dataclass(frozen=True)
class Usage:
    cost_usd: Optional[float] = None       # dollars the client itself reported; None when it reports none
    tokens: Dict[str, int] = field(default_factory=dict)
    session: Optional[str] = None
    final_message: str = ""
    reported_error: bool = False
    parsed: bool = False                    # False when no usable result was found in the output


def _events(output: str) -> Iterator[Dict[str, Any]]:
    """JSON objects in the output: one document, or one per line; other lines are skipped."""
    text = output.strip()
    if not text:
        return
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        yield whole
        return
    if isinstance(whole, list):
        yield from (item for item in whole if isinstance(item, dict))
        return
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            yield item


def _token_counts(raw: Any) -> Dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {name: int(raw[name]) for name in TOKEN_FIELDS
            if isinstance(raw.get(name), (int, float)) and not isinstance(raw.get(name), bool)}


def _add(left: Dict[str, int], right: Dict[str, int]) -> Dict[str, int]:
    return {name: left.get(name, 0) + right.get(name, 0) for name in list(left) + [n for n in right if n not in left]}


def parse_claude(output: str) -> Usage:
    """`claude -p` with `--output-format json` (one object) or `stream-json` (the last result event)."""
    result: Optional[Dict[str, Any]] = None
    for event in _events(output):
        if event.get("type") == "result" or ("result" in event and "session_id" in event):
            result = event
    if result is None:
        return Usage()
    cost = result.get("total_cost_usd")
    message = result.get("result")
    return Usage(
        cost_usd=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        tokens=_token_counts(result.get("usage")),
        session=result.get("session_id") if isinstance(result.get("session_id"), str) else None,
        final_message=message if isinstance(message, str) else "",
        reported_error=bool(result.get("is_error")),
        parsed=True,
    )


def parse_codex(output: str, last_message: str = "") -> Usage:
    """`codex exec --json`: tokens are summed over every completed turn; Codex reports no dollars."""
    tokens: Dict[str, int] = {}
    session: Optional[str] = None
    message, failed, seen = "", False, False
    for event in _events(output):
        kind = event.get("type")
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            session, seen = event["thread_id"], True
        elif kind == "turn.completed":
            tokens, seen = _add(tokens, _token_counts(event.get("usage"))), True
        elif kind in ("turn.failed", "error"):
            failed, seen = True, True
        elif kind == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                message, seen = item["text"], True
    return Usage(None, tokens, session, last_message.strip() or message, failed, seen)
