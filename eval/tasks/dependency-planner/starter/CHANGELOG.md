# Release notes

## 1.4 — reusable artifact snapshots

A completed job is an immutable, usable artifact snapshot. Encountering it during
planning ends traversal at that job: its historical prerequisites need not exist or
be enabled in today's catalog. The job itself must still exist in the catalog.
Snapshots remain usable when the producing job is disabled; disabling prevents
execution, not reuse. This also applies when the snapshot is an explicit target.

`Plan.reused` includes only completed jobs actually encountered while resolving the
requested targets, once each, in name order. Unrelated completed names (including
names from old catalogs) are ignored. An empty request therefore has no reused jobs.

## 1.3 — preview response

Preview exposes `run` and `reused` as lists. The internal `Plan` keeps tuples, so it
can be retained as a value without a caller mutating it through a response object.
