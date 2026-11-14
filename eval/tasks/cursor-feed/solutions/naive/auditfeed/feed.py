"""History endpoint using event time and sequence as a stable ordering key."""
from .tokens import CursorError, pack, unpack


def list_events(store, tenant, limit=50, cursor=None, kind=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 through 100")
    after = None
    if cursor is not None:
        state = unpack(cursor)
        after = state.get("after")
        if (set(state) != {"after"} or not isinstance(after, list)
                or len(after) != 2 or any(type(part) is not int for part in after)):
            raise CursorError("unsupported history cursor")
    candidates = store.scan(tenant, after, limit + 1, kind)
    items = candidates[:limit]
    next_cursor = None
    if len(candidates) > limit:
        next_cursor = pack({"after": [items[-1]["happened"], items[-1]["seq"]]})
    return {"items": items, "next_cursor": next_cursor}
