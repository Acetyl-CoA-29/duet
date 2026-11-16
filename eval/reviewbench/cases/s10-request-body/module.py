"""Read and decode a JSON request body, refusing bodies over a size limit."""
import json
from typing import Any, BinaryIO, Dict, Sequence

DEFAULT_MAX_BYTES = 1_048_576


class BodyError(Exception):
    """The request body cannot be used."""
    status = 400


class BodyTooLarge(BodyError):
    status = 413


def read_body(stream: BinaryIO, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    """Return the request body, or raise BodyTooLarge when it is over `max_bytes`."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    data = stream.read()
    if len(data) > max_bytes:
        raise BodyTooLarge(f"the body is larger than {max_bytes} bytes")
    return data


def parse_json_body(stream: BinaryIO, max_bytes: int = DEFAULT_MAX_BYTES) -> Dict[str, Any]:
    """Decode the body as a JSON object."""
    data = read_body(stream, max_bytes)
    if not data.strip():
        raise BodyError("the body is empty")
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BodyError(f"the body is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise BodyError("the body must be a JSON object")
    return decoded


def require_fields(body: Dict[str, Any], names: Sequence[str]) -> Dict[str, Any]:
    """The named fields of `body`; raises BodyError listing any that are missing."""
    missing = [name for name in names if name not in body]
    if missing:
        raise BodyError("missing field(s): " + ", ".join(missing))
    return {name: body[name] for name in names}
