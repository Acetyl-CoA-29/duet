"""Print one session of the review micro-benchmark: rates per condition, what a second look added,
the decision rule's verdict, and what the calls cost."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from evallib.summary import wilson

from . import analysis, store
from .analysis import AGAINST, INCONCLUSIVE, MIN_UNITS, STRONGER, SUPPORT_NET, SUPPORTS, TOO_FEW, Paired
from .cases import Case
from .clients import REVIEWERS, other
from .plan import COND_CROSS, COND_SAME, COND_SELF, CONDITIONS
from .score import LINE_TOLERANCE

RATE_HEADERS = ("author", "condition", "reviewer", "strict catch", "95% interval", "lenient catch",
                "false alarms", "items/review", "unparsed", "missing")
USAGE_HEADERS = ("reviewer", "calls", "failed", "$ reported", "tokens reported", "seconds")
EXPLAINED = {
    SUPPORTS: f"in both directions a cross-lab second look rescued at least {SUPPORT_NET} more seeded defects than a "
              "same-lab one. Suggestive only: worth the fuller comparison, not proof.",
    AGAINST: "in neither direction did a cross-lab second look rescue more than a same-lab one. On this test a "
             "fresh session of the same model does as well.",
    STRONGER: "the cross-lab look helped one author and hurt the other by a similar margin: one model is simply "
              "the stronger reviewer here, which is not evidence for pairing labs.",
    INCONCLUSIVE: f"the differences are smaller than {SUPPORT_NET} cases or point different ways; with this many "
                  "cases that is noise.",
    TOO_FEW: f"fewer than {MIN_UNITS} complete seeded units in some direction; finish the run before reading it.",
}
NOTES = (
    "strict: one reported item both cites the keyed lines (within "
    f"{LINE_TOLERANCE}) and matches a keyed keyword. lenient: some item does either.",
    "false alarms: clean control cases on which anything was reported.",
    "fresh-cross for one author and fresh-same for the other are the same reviews, seen from each side.",
)


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * width for width in widths])] + [line(row) for row in rows])


def share(hits: int, total: int) -> str:
    return f"{hits}/{total} ({100 * hits / total:.0f}%)" if total else "-"


def _interval(hits: int, total: int) -> str:
    bounds = wilson(hits, total)
    return "-" if bounds is None else f"{100 * bounds[0]:.0f}-{100 * bounds[1]:.0f}%"


def _rate_row(stats: analysis.ConditionStats) -> Sequence[str]:
    reviewer = other(stats.author) if stats.condition == COND_CROSS else stats.author
    reviews = stats.seeded + stats.controls
    return (stats.author, stats.condition, reviewer, share(stats.strict, stats.seeded),
            _interval(stats.strict, stats.seeded), share(stats.lenient, stats.seeded),
            share(stats.false_alarms, stats.controls), f"{stats.items / reviews:.1f}" if reviews else "-",
            str(stats.unparsed), str(stats.missing))


def _names(units: Sequence[str]) -> str:
    return ", ".join(sorted(units)) if units else "none"


def _second_look(pair: Paired, label: str) -> List[str]:
    p_value = "-" if pair.p_value is None else f"{pair.p_value:.2f}"
    return [
        f"author {pair.author} ({label}; {pair.units} complete seeded unit(s)):",
        f"  caught by self: {len(pair.caught[COND_SELF])}   by fresh-same: {len(pair.caught[COND_SAME])}   "
        f"by fresh-cross: {len(pair.caught[COND_CROSS])}",
        f"  rescued from self by fresh-same  ({len(pair.rescued_same)}): {_names(pair.rescued_same)}",
        f"  rescued from self by fresh-cross ({len(pair.rescued_cross)}): {_names(pair.rescued_cross)}",
        f"  net (cross - same): {pair.net:+d}   sign test p = {p_value}",
        f"  caught only by cross-lab review ({len(pair.only_cross)}): {_names(pair.only_cross)}",
        f"  caught only by same-lab fresh review ({len(pair.only_same)}): {_names(pair.only_same)}",
    ]


def _usage_rows(totals: Mapping[str, Mapping[str, Any]]) -> List[Sequence[str]]:
    return [(reviewer, str(use["calls"]), str(use["failed"]),
             "none reported" if use["cost_usd"] is None else f"${use['cost_usd']:.2f}",
             "-" if use["tokens"] is None else f"{use['tokens']:,}", f"{use['seconds']:.0f}")
            for reviewer, use in totals.items()]


def _warnings(scored: Mapping[Any, analysis.Scored], records: Mapping[str, store.Record],
              totals: Mapping[str, Mapping[str, Any]], invocations: Sequence[Mapping[str, Any]]) -> List[str]:
    notes: List[str] = []
    stopped = [str(item["stopped"]) for item in invocations[-1:] if item.get("stopped")]
    notes += [f"INCOMPLETE: the last invocation stopped early ({reason})." for reason in stopped]
    failed = sum(1 for record in records.values() if record.get("status") != store.OK)
    if failed:
        notes.append(f"{failed} cell(s) have no usable reply (error or half-done); they count as missing.")
    leaks = sum(use["leaks"] for use in totals.values())
    if leaks:
        notes.append(f"CONTAMINATED? {leaks} call(s) printed the name of hidden material; read their logs.")
    commands = sum(use["commands_run"] for use in totals.values())
    if commands:
        notes.append(f"A Codex reviewer ran shell commands (about {commands} event(s)) despite being told not to.")
    conflicts = sum(1 for item in scored.values() if item.review.conflict)
    if conflicts:
        notes.append(f"{conflicts} reply(ies) said DEFECTS: NONE and also listed items; the items were scored.")
    dropped = sum(1 for item in scored.values() if item.review.dropped)
    if dropped:
        notes.append(f"{dropped} reply(ies) listed more items than allowed; the extra items were not scored.")
    return notes


def _retest(scored: Mapping[Any, analysis.Scored], cases: Sequence[Case], samples: int) -> List[str]:
    if samples < 2:
        return ["Noise floor: not measured (one sample per cell). Run with --samples 2 to see how often the "
                "same condition disagrees with itself."]
    rows = analysis.retest_disagreement(scored, cases, samples)
    return ["Noise floor (seeded cases where samples of the same condition disagreed, strict):"] + [
        f"  {reviewer} {mode}: {share(differ, total)}" for (reviewer, mode), (differ, total) in rows.items()]


def samples_of(invocations: Sequence[Mapping[str, Any]], records: Mapping[str, store.Record]) -> int:
    recorded = [int(record.get("sample", 1)) for record in records.values()]
    planned = [int(item.get("settings", {}).get("samples", 1)) for item in invocations]
    return max([1, *recorded, *planned])


def format_session(session_dir: Path, cases: Sequence[Case], title: Optional[str] = None) -> str:
    records = store.load_records(session_dir)
    invocations = store.load_invocations(session_dir)
    samples = samples_of(invocations, records)
    scored = analysis.score_session(records, {case.name: case for case in cases})
    totals = analysis.usage_by_reviewer(store.load_ledger(session_dir))
    seeded = sum(1 for case in cases if not case.control)
    rates = [_rate_row(analysis.condition_stats(scored, author, condition, cases, samples))
             for author in REVIEWERS for condition in CONDITIONS]
    by_scoring: Dict[str, List[Paired]] = {
        label: [analysis.paired(scored, author, cases, samples, strict) for author in REVIEWERS]
        for label, strict in (("strict", True), ("lenient", False))}
    verdicts = {label: analysis.verdict(pairs) for label, pairs in by_scoring.items()}
    overall = verdicts["strict"] if len(set(verdicts.values())) == 1 else INCONCLUSIVE
    agreement = "" if len(set(verdicts.values())) == 1 else " (the two scorings disagree, so the result depends on the scoring)"
    parts = [
        title or f"Review micro-benchmark: session {session_dir.name}",
        f"{seeded} seeded case(s), {len(cases) - seeded} control(s), {samples} sample(s) per cell",
        table(RATE_HEADERS, rates),
        "\n".join(NOTES),
        "What a second look added to the author's own review",
        *("\n".join(_second_look(pair, label)) for label, pairs in by_scoring.items() for pair in pairs),
        "\n".join(_retest(scored, cases, samples)),
        f"Decision rule: strict scoring says {verdicts['strict']}, lenient says {verdicts['lenient']}.\n"
        f"Verdict: {overall}{agreement}: {EXPLAINED[overall]}",
        "Usage (as each client reported it; dollars and tokens are not comparable across labs)\n"
        + table(USAGE_HEADERS, _usage_rows(totals)),
        *(["\n".join(_warnings(scored, records, totals, invocations))]
          if _warnings(scored, records, totals, invocations) else []),
    ]
    return "\n\n".join(parts)
