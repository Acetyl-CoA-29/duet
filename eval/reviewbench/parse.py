"""Read a reviewer's reply into a verdict and a list of findings. Tolerant of decoration (bold,
backticks, bullets) but strict about substance: a reply with neither `DEFECTS: NONE` nor a list
item is `unparsed`, and is never guessed at."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .prompts import MAX_ITEMS

NONE, DEFECTS, UNPARSED = "none", "defects", "unparsed"
LineRange = Tuple[int, int]

_DECORATION = re.compile(r"\*\*|`")
_HEADER = re.compile(r"^\s*#*\s*defects?\s*:\s*(.*)$", re.IGNORECASE)
_NONE = re.compile(r"^none\b", re.IGNORECASE)
_NUMBERED = re.compile(r"^\s*(?:[-*•]\s*)?\(?\d{1,2}[.):]\s+(\S.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(\S.*)$")
_RANGE_TAIL = r"(?:\s*(?:-|–|—|to|through)\s*(?:lines?\s*|l)?(\d{1,4}))?"
_LINE_REF = re.compile(r"\b(?:lines?\s*\.?\s*|l)(\d{1,4})" + _RANGE_TAIL, re.IGNORECASE)
_BARE_REF = re.compile(r"^\s*(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?\s*:")


@dataclass(frozen=True)
class Finding:
    lines: Tuple[LineRange, ...]     # empty when the item names no line
    text: str


@dataclass(frozen=True)
class Review:
    verdict: str                     # NONE, DEFECTS or UNPARSED
    findings: Tuple[Finding, ...] = ()
    conflict: bool = False           # said `DEFECTS: NONE` and listed items; the items are kept
    dropped: int = 0                 # items beyond the allowed number, which are not scored


def line_refs(text: str) -> Tuple[LineRange, ...]:
    """Every line or line range an item names: `L12`, `L12-L14`, `line 12`, `lines 12 to 14`, or a
    bare `12-14:` at the start. A reversed range is put in order."""
    found = _LINE_REF.findall(text)
    if not found:
        bare = _BARE_REF.match(text)
        found = [bare.groups()] if bare else []
    ranges: List[LineRange] = []
    for first, last in found:
        low, high = sorted((int(first), int(last or first)))
        if (low, high) not in ranges:
            ranges.append((low, high))
    return tuple(ranges)


def _item_start(line: str) -> Optional[str]:
    """The text of a list item this line opens, or None. A plain bullet counts only when it starts
    with a line reference, so prose bullets are not mistaken for findings."""
    numbered = _NUMBERED.match(line)
    if numbered:
        return numbered.group(1)
    bullet = _BULLET.match(line)
    if bullet and (_LINE_REF.match(bullet.group(1)) or _BARE_REF.match(bullet.group(1))):
        return bullet.group(1)
    return None


def _split(reply: str) -> Tuple[bool, List[str]]:
    """(whether a `DEFECTS: NONE` line was seen, the text of each item with its continuation lines)."""
    said_none, item_open = False, False
    items: List[str] = []
    for raw in reply.splitlines():
        line = _DECORATION.sub("", raw).rstrip()
        header = _HEADER.match(line)
        if header:
            said_none = said_none or bool(_NONE.match(header.group(1).strip()))
            line = "" if _NONE.match(header.group(1).strip()) else header.group(1)
        started = _item_start(line) if line.strip() else None
        if started is not None:
            items.append(started.strip())
            item_open = True
        elif line.strip() and item_open:
            items[-1] = f"{items[-1]} {line.strip()}"     # a wrapped item; a blank line ends it
        else:
            item_open = False
    return said_none, items


def parse_review(reply: str) -> Review:
    said_none, items = _split(reply)
    if not items:
        return Review(NONE if said_none else UNPARSED)
    findings = tuple(Finding(line_refs(text), text) for text in items[:MAX_ITEMS])
    return Review(DEFECTS, findings, conflict=said_none, dropped=max(0, len(items) - MAX_ITEMS))
