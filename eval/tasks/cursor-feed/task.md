Support reports missing audit events when clicking **Load more** after a bulk import.
`tests/test_feed.py::test_imported_events_are_not_skipped` reproduces it. Repair the
pagination used by the history endpoint and downloadable export, preserving the public
interfaces and existing export format. This is a server-side fix; there is no frontend here.

The package README, integration notes and release notes describe the supported workflow.
Keep the implementation compatible with Python 3.9 and use only the standard library.
