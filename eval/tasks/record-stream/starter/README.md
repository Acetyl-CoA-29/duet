# Record stream ingest

This package is shared by the batch importer and the live-upload endpoint. A transport
adapter supplies byte chunks; those chunks have no relationship to record boundaries.
The public entry points are exported by `recordstream/__init__.py`.

- `RecordDecoder` is the common parser.
- `iter_records` consumes a finite iterable of byte chunks.
- `read_file` adapts a binary file object without taking ownership of it.
- `UploadSession` receives a live request through separate `push` calls and `close`.
- `summarize` projects records for the import dashboard.

`docs/protocol.md` describes the wire format and decoder lifecycle. The release history
records compatibility commitments still used by old exporters. Tests include the newest
chunking regression as well as behavior relied on by the dashboard.

Run `python3 -m pytest -q -p no:cacheprovider tests` from this directory.
