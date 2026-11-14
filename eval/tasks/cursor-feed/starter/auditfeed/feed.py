"""History endpoint. Keep this callable independent of an HTTP framework."""
from .tokens import CursorError, pack, unpack


def list_events(store, tenant, limit=50, cursor=None, kind=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 through 100")
    before = None
    if cursor is not None:
        state = unpack(cursor)
        if set(state) != {"before"} or type(state["before"]) is not int:
            raise CursorError("unsupported history cursor")
        before = state["before"]
    candidates = store.rows_before(tenant, before, limit + 1, kind)
    items = candidates[:limit]
    next_cursor = None
    if len(candidates) > limit:
        next_cursor = pack({"before": items[-1]["happened"]})
    return {"items": items, "next_cursor": next_cursor}
