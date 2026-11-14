# Author notes — layered-settings

This file, hidden tests, and solution overlays are outside `starter/` and are not given to
an arm. Only the ticket and starter tree are part of the challenge.

The engineer must replace inline readers with one profile-aware resolver while preserving
public row formats. The plausible wrong solution recursively merges already-defaulted
profile snapshots. That passes the visible single-parent rollout smoke test, handles basic
nesting and cycles, and preserves old inline settings, yet cannot represent sparse edits,
label deletion, or shared-ancestor first-visit ordering correctly.

| Oracle requirement | Evidence available to the engineer |
| --- | --- |
| Complete output keys, factory defaults, numeric types/ranges | `docs/configuration.md` table; `schema.py`; compatibility tests |
| Sparse inheritance; explicit defaults differ from missing fields | Contract's absent/present rules; changelog's reusable edits and factory-once rule |
| Parent ordering and service override | `CHANGELOG.md` 0.9, first paragraph |
| Diamond/repeated-parent deduplication; fresh visited state per service | Same paragraph explicitly says first visit, shared ancestors, duplicate entries, and fresh history |
| False/zero/empty list/empty string retained | Contract's present-value paragraph; compatibility tests |
| Whole-group/field resets and label deletion | Contract's reset and label paragraphs; existing `apply_layer` implementation |
| Validate effective values after all edits; reject invalid layer shapes | Contract's validation paragraph; separate `apply_layer` and `validate` APIs |
| Cycle/missing-profile failure, unknown-service KeyError, closure locality | Contract's error paragraph |
| Profile and service namespaces remain distinct | Contract's opening paragraph (extends names profiles only) |
| Independent nested containers, no input mutation or stale cached values | README pure API contract; contract's final paragraph; compatibility test |
| Launch filters disabled; report includes disabled; errors propagate; lexical order | `CHANGELOG.md` 0.9 second paragraph; consumer docstrings; compatibility test |
| Existing consumer output fields unchanged | Ticket; `launch.py`, `report.py`, compatibility test |

The oracle does not depend on private helper names, traversal implementation, exact error
text, timing, filesystem behavior, or unstated limits. All inputs are in-memory standard
Python dictionaries/lists. Valid document structure (services/profile mappings) is assumed;
malformed documents outside the layer contract are not scored.

Estimated uncalibrated solo hidden-suite pass rate: **50%**. This is a hypothesis, not a
measurement: no model calls were made. The distinction between applying edits and merging
snapshots and the separate operator-report call site are the intended sources of misses.
Calibrate only on solo runs before making claims about separation between arms.
