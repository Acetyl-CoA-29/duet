"""Turn a session's recorded replies into numbers, and apply the decision rule stated in the README.

Replies are parsed and scored here, at summary time, from the stored text: a change to the scoring
never needs a paid call to be repeated.

The unit of comparison is a (seeded case, sample). For an author model X the question is what a
second look adds to X's own review:
  rescued by fresh-same   caught by a fresh session of X, missed by X's `self` review
  rescued by fresh-cross  caught by a fresh session of the other model, missed by X's `self` review
  net = rescued by fresh-cross - rescued by fresh-same
Comparing fresh-cross with fresh-same directly would only ask which model is the better reviewer,
with the opposite answer in the other direction; the net above can be positive in both."""
from __future__ import annotations

from dataclasses import dataclass
from math import comb
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from evallib import usage

from . import store
from .cases import Case
from .clients import REVIEWERS
from .parse import Review, parse_review
from .plan import COND_CROSS, COND_SAME, COND_SELF, CONDITIONS, MODES, Cell, condition_cell
from .score import Score, score_review

# The decision rule's numbers. They are fixed here, before any run, and repeated in the README.
SUPPORT_NET = 3          # cases a cross-lab second look must add over a same-lab one, per direction
MIN_UNITS = 12           # complete (case, sample) units needed per direction before saying anything
SUPPORTS, AGAINST, STRONGER, INCONCLUSIVE, TOO_FEW = "SUPPORTS", "AGAINST", "ONE-SIDED", "INCONCLUSIVE", "TOO FEW"


@dataclass(frozen=True)
class Scored:
    cell: Cell
    review: Review
    score: Score


@dataclass(frozen=True)
class ConditionStats:
    author: str
    condition: str
    seeded: int = 0          # seeded reviews with a recorded reply
    strict: int = 0
    lenient: int = 0
    controls: int = 0
    false_alarms: int = 0
    unparsed: int = 0
    items: int = 0           # findings reported over all those reviews
    missing: int = 0         # planned reviews with no usable reply


@dataclass(frozen=True)
class Paired:
    author: str
    units: int                           # seeded (case, sample) units with all three conditions present
    caught: Mapping[str, FrozenSet[str]]   # condition -> units caught
    only_cross: FrozenSet[str]           # caught by fresh-cross, not by fresh-same
    only_same: FrozenSet[str]
    rescued_cross: FrozenSet[str]        # missed by self, caught by fresh-cross
    rescued_same: FrozenSet[str]
    net: int
    p_value: Optional[float]             # two-sided sign test on the units only one second look rescued


def score_session(records: Mapping[str, store.Record], cases: Mapping[str, Case]) -> Dict[Cell, Scored]:
    """Parse and score every finished cell whose case is known."""
    scored: Dict[Cell, Scored] = {}
    for record in records.values():
        case = cases.get(str(record.get("case")))
        if record.get("status") != store.OK or case is None:
            continue
        cell = Cell(case.name, str(record.get("reviewer")), str(record.get("mode")), int(record.get("sample", 1)))
        review = parse_review(str(record.get("reply", "")))
        scored[cell] = Scored(cell, review, score_review(case, review))
    return scored


def unit_label(case: str, sample: int, samples: int) -> str:
    return case if samples == 1 else f"{case}#{sample}"


def condition_stats(scored: Mapping[Cell, Scored], author: str, condition: str,
                    cases: Sequence[Case], samples: int) -> ConditionStats:
    stats: Dict[str, int] = {}

    def add(name: str, amount: int = 1) -> None:
        stats[name] = stats.get(name, 0) + amount

    for case in cases:
        for sample in range(1, samples + 1):
            found = scored.get(condition_cell(author, condition, case.name, sample))
            if found is None:
                add("missing")
                continue
            add("controls" if case.control else "seeded")
            add("items", found.score.reported)
            add("unparsed", 0 if found.score.parsed else 1)
            add("strict", int(found.score.strict))
            add("lenient", int(found.score.lenient))
            add("false_alarms", int(found.score.false_alarm))
    return ConditionStats(author, condition, **stats)


def sign_test(first: int, second: int) -> Optional[float]:
    """Two-sided exact sign test for a `first` against `second` split; None when there is nothing to test."""
    total = first + second
    if total == 0:
        return None
    tail = sum(comb(total, k) for k in range(0, min(first, second) + 1)) / 2 ** total
    return min(1.0, 2 * tail)


def paired(scored: Mapping[Cell, Scored], author: str, cases: Sequence[Case], samples: int,
           strict: bool = True) -> Paired:
    caught: Dict[str, List[str]] = {condition: [] for condition in CONDITIONS}
    units = 0
    for case in (case for case in cases if not case.control):
        for sample in range(1, samples + 1):
            found = {condition: scored.get(condition_cell(author, condition, case.name, sample))
                     for condition in CONDITIONS}
            if any(item is None for item in found.values()):
                continue
            units += 1
            for condition, item in found.items():
                if item is not None and (item.score.strict if strict else item.score.lenient):
                    caught[condition].append(unit_label(case.name, sample, samples))
    sets = {condition: frozenset(names) for condition, names in caught.items()}
    rescued_cross = sets[COND_CROSS] - sets[COND_SELF]
    rescued_same = sets[COND_SAME] - sets[COND_SELF]
    return Paired(author, units, sets, sets[COND_CROSS] - sets[COND_SAME], sets[COND_SAME] - sets[COND_CROSS],
                  rescued_cross, rescued_same, len(rescued_cross) - len(rescued_same),
                  sign_test(len(rescued_cross - rescued_same), len(rescued_same - rescued_cross)))


def verdict(pairs: Sequence[Paired]) -> str:
    """The decision rule. It needs every direction, and enough complete units in each."""
    if len(pairs) < len(REVIEWERS) or any(pair.units < MIN_UNITS for pair in pairs):
        return TOO_FEW
    nets = [pair.net for pair in pairs]
    if all(net >= SUPPORT_NET for net in nets):
        return SUPPORTS
    if all(net <= 0 for net in nets):
        return AGAINST
    if max(nets) >= SUPPORT_NET and min(nets) <= -SUPPORT_NET:
        return STRONGER
    return INCONCLUSIVE


def retest_disagreement(scored: Mapping[Cell, Scored], cases: Sequence[Case],
                        samples: int) -> Dict[Tuple[str, str], Tuple[int, int]]:
    """With two or more samples: for each (reviewer, mode), in how many seeded cases the samples of
    the very same condition disagreed on the strict score, out of the cases with every sample present.
    This is the noise floor any difference between conditions has to clear."""
    result: Dict[Tuple[str, str], Tuple[int, int]] = {}
    for reviewer in REVIEWERS:
        for mode in MODES:
            outcomes = [[scored.get(Cell(case.name, reviewer, mode, sample)) for sample in range(1, samples + 1)]
                        for case in cases if not case.control]
            complete = [[item.score.strict for item in row if item is not None] for row in outcomes
                        if all(item is not None for item in row)]
            result[(reviewer, mode)] = (sum(1 for row in complete if len(set(row)) > 1), len(complete))
    return result


def usage_by_reviewer(ledger: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Calls, failures, dollars the client reported and tokens it reported, per reviewer."""
    totals: Dict[str, Dict[str, Any]] = {}
    for reviewer in REVIEWERS:
        entries = [entry for entry in ledger if entry.get("reviewer") == reviewer]
        costs = [entry["cost_usd"] for entry in entries if isinstance(entry.get("cost_usd"), (int, float))]
        tokens = [usage.total_tokens(entry.get("tokens")) for entry in entries]
        totals[reviewer] = {
            "calls": len(entries), "failed": sum(1 for entry in entries if not entry.get("ok")),
            "cost_usd": float(sum(costs)) if costs else None,
            "tokens": sum(count for count in tokens if count is not None) if any(tokens) else None,
            "seconds": float(sum(entry.get("seconds") or 0.0 for entry in entries)),
            "leaks": sum(1 for entry in entries if entry.get("leaks")),
            "commands_run": sum(int(entry.get("commands_run") or 0) for entry in entries)}
    return totals
