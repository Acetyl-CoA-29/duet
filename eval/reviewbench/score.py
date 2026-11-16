"""Score one parsed review against a case's answer key. Done by code alone; no model judges a reply.

Strict:  one reported item both points at the keyed lines and matches a keyed keyword.
Lenient: some reported item does either. The gap between the two shows how much the result
         depends on the scoring rather than on the reviewers.
Control: any reported item is a false alarm."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple

from .cases import AnswerKey, Case
from .parse import UNPARSED, Finding, Review

# A reported line may be this many lines away from the keyed range and still count: reviewers often
# cite the enclosing statement or the line after the faulty one.
LINE_TOLERANCE = 2
# A reported range longer than this points at no line in particular (a whole function, the whole
# file) and never counts as a line match.
MAX_REPORTED_SPAN = 20


@dataclass(frozen=True)
class Score:
    control: bool
    parsed: bool
    reported: int            # items scored (after the cap on items)
    strict: bool = False
    lenient: bool = False
    line_hit: bool = False   # some item points at the keyed lines
    keyword_hit: bool = False
    false_alarm: bool = False


def lines_match(finding: Finding, key: AnswerKey, tolerance: int = LINE_TOLERANCE) -> bool:
    for low, high in finding.lines:
        if high - low + 1 > MAX_REPORTED_SPAN:
            continue
        if any(low <= last + tolerance and first - tolerance <= high for first, last in key.lines):
            return True
    return False


def keywords_match(finding: Finding, key: AnswerKey) -> bool:
    text = finding.text.lower()
    return any(re.search(pattern, text) for pattern in key.keywords)


def _hits(review: Review, key: AnswerKey, tolerance: int) -> Tuple[Tuple[bool, bool], ...]:
    return tuple((lines_match(finding, key, tolerance), keywords_match(finding, key))
                 for finding in review.findings)


def score_review(case: Case, review: Review, tolerance: int = LINE_TOLERANCE) -> Score:
    parsed, reported = review.verdict != UNPARSED, len(review.findings)
    if case.key is None:
        return Score(True, parsed, reported, false_alarm=reported > 0)
    hits = _hits(review, case.key, tolerance)
    return Score(False, parsed, reported,
                 strict=any(line and word for line, word in hits),
                 lenient=any(line or word for line, word in hits),
                 line_hit=any(line for line, _ in hits),
                 keyword_hit=any(word for _, word in hits))
