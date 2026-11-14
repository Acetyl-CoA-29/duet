# Audit history service

`Store` owns an append-only SQLite event log. Event times are integer milliseconds
supplied by clients; imports commonly contain equal times and historical backfills.
Sequence numbers are assigned by the database and never reused. Privacy deletion is
the only supported mutation. This package uses no clock or external service.

`list_events(store, tenant, limit=50, cursor=None, kind=None)` returns a dictionary
with `items` and `next_cursor`. Items contain seq, tenant, happened, kind and message.
Order is descending `(happened, seq)`. `kind=None` means all kinds; otherwise it is
an exact, case-sensitive match (the empty string is a real kind).

The UI treats cursors as opaque strings, and uses `None` to start again. A cursor
exists only if another matching item exists. The page size is an integer from 1
through 100; bool is not a page size. Invalid sizes raise ValueError. A page may
use a different size than the preceding page.

`export_lines` yields JSON lines, and `export_text` joins them. Both share the same
history semantics as the UI; batch_size obeys the same size rule. See
docs/integration.md for long-running reads and
CHANGELOG.md for backwards compatibility decisions.

Run the smoke and regression tests with `python3 -m pytest -q tests`.
