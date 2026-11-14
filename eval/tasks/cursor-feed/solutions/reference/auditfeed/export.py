"""Low-memory JSON-lines export used by the support download button."""
import json

from .feed import list_events


def export_lines(store, tenant, batch_size=50, kind=None):
    cursor = None
    while True:
        page = list_events(store, tenant, batch_size, cursor, kind)
        for row in page["items"]:
            yield json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        cursor = page["next_cursor"]
        if cursor is None:
            return


def export_text(store, tenant, batch_size=50, kind=None):
    return "".join(export_lines(store, tenant, batch_size, kind))
