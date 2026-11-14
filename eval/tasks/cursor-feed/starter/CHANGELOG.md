# Release notes

## 1.4
- Added optional exact kind filtering to history and export.
- Page size may change during a browse session without restarting it.
- Privacy deletion must not invalidate outstanding browsing positions.

## 1.3
- `Store.rows_before(tenant, before, limit, kind=None)` is still used by an external
  reporting script: `before` is an exclusive integer event-time bound, None is
  unbounded. Keep its arguments and meaning. Add another store operation if needed.
- Cursors are internal to this release; preserving their previous wire encoding
  is not required. A successfully returned cursor must remain reusable and must
  round-trip through a fresh service instance backed by the same database.
