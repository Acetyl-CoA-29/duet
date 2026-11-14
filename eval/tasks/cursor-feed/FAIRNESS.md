# Author notes: cursor-feed

This file stays outside the starter and is never part of an arm's prompt.

The task asks for a production pagination repair; the visible failure only exposes
same-time imports. A rushed but competent patch adds a sequence tie-breaker to the
history endpoint. That is the naive solution. It still admits backfilled arrivals,
does not bind continuation to filters, and leaves the export's legacy query alone.
The reference adds a separate snapshot scan, self-contained query-bound tokens,
and routes exports through history pagination while preserving the old store API.

| Hidden behavior | Evidence available to the engineer |
| --- | --- |
| Descending time/sequence, tenant isolation, exact kind including empty string | README API contract; store query; visible imported-event and filter tests |
| Snapshot excludes all later arrivals, including backfills, across every page | docs/integration.md opening paragraph; Store.high_watermark and sequence contract |
| Deleted boundary still resumable; deleted future rows absent | docs/integration.md; CHANGELOG 1.4 |
| Tokens tied to tenant and kind; replay and variable page size | docs/integration.md; README; CHANGELOG 1.3/1.4 |
| Terminal and empty pages; limit validation; token errors | README; docs/integration.md; existing validation and CursorError |
| Tokens work in fresh process | CHANGELOG 1.3: round-trip through fresh service on same DB |
| Export uses same order, cutoff, filter and size rules | README; ticket names export; export.py contains the second query call site |
| Snapshot starts on iteration; no transaction held over yield; stable Unicode JSONL | docs/integration.md; visible export test |
| Existing timestamp reporting helper remains exclusive | CHANGELOG 1.3; helper docstring; visible legacy reporting test |

All tests are deterministic, use in-memory or temporary SQLite and run without
network, clocks, platform-specific filenames or model calls. The oracle does not
require a particular token layout or algorithm, beyond documented observable behavior.

Estimated solo hidden-suite pass rate: **45%** (uncalibrated). Difficulty comes
from reconciling arrival order with event-time order, preserving a legacy call site,
and propagating the session across an independent generator consumer. Calibration
requires repeated solo runs; the estimate is not evidence of arm separation.
