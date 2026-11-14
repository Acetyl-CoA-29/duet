# Runqueue

Runqueue turns a catalog of build jobs into a serial execution plan. The release web
service calls `runqueue.preview.preview`; the queue consumer calls
`runqueue.worker.submission`. Neither function executes a job or reads the network.

The public scheduling API is `build_plan(catalog, requested, completed=())`.
`catalog` maps a job's name to its `Job` record. Keys match the record's name, and names
are nonempty strings. Dependency and ordering entries are job names, not patterns.
Callers supply correctly typed records; validating the catalog's schema is out of scope.

Read `docs/planning.md` for scheduling semantics and `CHANGELOG.md` for cache behavior.
The tests include compatibility promises made to the queue consumer.

Run the local regression suite with `python3 -m pytest -q -p no:cacheprovider tests`.
