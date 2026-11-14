"""Low-memory JSON-lines export used by the support download button."""
import json


def export_lines(store, tenant, batch_size=50, kind=None):
    before = None
    while True:
        rows = store.rows_before(tenant, before, batch_size, kind)
        if not rows:
            return
        for row in rows:
            yield json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        before = rows[-1]["happened"]


def export_text(store, tenant, batch_size=50, kind=None):
    return "".join(export_lines(store, tenant, batch_size, kind))
