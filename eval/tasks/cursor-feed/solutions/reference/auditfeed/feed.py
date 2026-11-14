"""History endpoint with replayable snapshot continuation."""
from .tokens import CursorError, pack, unpack


def _resume(cursor, tenant, kind):
    state = unpack(cursor)
    fields = {"v", "tenant", "kind", "watermark", "after"}
    after = state.get("after")
    if (set(state) != fields or type(state["v"]) is not int or state["v"] != 1
            or state["tenant"] != tenant or state["kind"] != kind
            or type(state["watermark"]) is not int
            or not 0 <= state["watermark"] < 2 ** 63
            or not isinstance(after, list) or len(after) != 2
            or any(type(part) is not int for part in after)
            or not -(2 ** 63) <= after[0] < 2 ** 63
            or not 0 < after[1] <= state["watermark"]):
        raise CursorError("unsupported or mismatched history cursor")
    return state


def list_events(store, tenant, limit=50, cursor=None, kind=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 through 100")
    if cursor is None:
        state = {"v": 1, "tenant": tenant, "kind": kind,
                 "watermark": store.high_watermark(), "after": None}
    else:
        state = _resume(cursor, tenant, kind)
    rows = store.scan(tenant, state["watermark"], state["after"], limit + 1, kind)
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        state["after"] = [last["happened"], last["seq"]]
        next_cursor = pack(state)
    return {"items": items, "next_cursor": next_cursor}
