The ingest service now receives export data in transport chunks instead of whole lines.
Customers report intermittent parse errors and missing final records, depending on the
uploader. Repair the record-stream package and its consumers so the existing wire and
lifecycle contracts hold for streamed input. Preserve the public API and existing behavior.

The workspace contains the package, its protocol notes, release history, and regression
tests. Use only the Python standard library. Do not modify the supplied tests.
