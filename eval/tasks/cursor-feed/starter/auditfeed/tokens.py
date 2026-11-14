"""Private transport encoding; consumers must not interpret these strings."""
import base64
import json


class CursorError(ValueError):
    pass


def pack(state):
    body = json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii")


def unpack(token):
    try:
        if not isinstance(token, str):
            raise ValueError("not a string")
        body = base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True)
        state = json.loads(body.decode("utf-8"))
        if not isinstance(state, dict):
            raise ValueError("not an object")
        return state
    except (ValueError, TypeError, UnicodeError) as error:
        raise CursorError("invalid history cursor") from error
