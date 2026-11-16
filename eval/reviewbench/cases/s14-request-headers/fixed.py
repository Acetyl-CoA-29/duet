"""Build the headers for requests to the partner API."""
from typing import Dict, Mapping, Optional

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "User-Agent": "orders-service/2.3",
}
FORBIDDEN_OVERRIDES = frozenset({"host", "content-length", "transfer-encoding"})
REDACTED = "***"


def check_header(name: str, value: str) -> None:
    """Refuse names and values that could split or smuggle a header."""
    if not name or any(character in name for character in " :\r\n"):
        raise ValueError(f"bad header name: {name!r}")
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        raise ValueError(f"bad value for header {name}")
    if name.lower() in FORBIDDEN_OVERRIDES:
        raise ValueError(f"the {name} header is set by the transport")


def build_headers(token: Optional[str] = None, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Headers for one request: the defaults, then the bearer token, then the caller's extras."""
    headers = dict(DEFAULT_HEADERS)
    if token:
        check_header("Authorization", token)
        headers["Authorization"] = f"Bearer {token}"
    for name, value in (extra or {}).items():
        check_header(name, value)
        headers[name] = value
    return headers


def redact(headers: Mapping[str, str]) -> Dict[str, str]:
    """A copy that is safe to log: credentials are replaced."""
    secret_names = ("authorization", "cookie", "x-api-key")
    return {name: REDACTED if name.lower() in secret_names else value for name, value in headers.items()}


def describe(headers: Mapping[str, str]) -> str:
    """One `Name: value` line per header, sorted by name, with credentials hidden."""
    return "\n".join(f"{name}: {value}" for name, value in sorted(redact(headers).items()))
