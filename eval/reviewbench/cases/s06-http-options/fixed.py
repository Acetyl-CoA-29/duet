"""Turn a raw configuration mapping into checked options for the outbound HTTP client."""
from typing import Any, Mapping, NamedTuple

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_REDIRECTS = 5
MAX_REDIRECTS = 20
DEFAULT_USER_AGENT = "inventory-sync/1.0"


class OptionsError(ValueError):
    """The configuration holds a value the client cannot use."""


class HttpOptions(NamedTuple):
    base_url: str
    timeout_seconds: float
    max_redirects: int
    verify_tls: bool
    user_agent: str


def _number(raw: Mapping[str, Any], name: str, default: float, low: float, high: float) -> float:
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OptionsError(f"{name} must be a number")
    if not low <= value <= high:
        raise OptionsError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def _flag(raw: Mapping[str, Any], name: str, default: bool) -> bool:
    value = raw.get(name, default)
    if not isinstance(value, bool):
        raise OptionsError(f"{name} must be true or false")
    return value


def _text(raw: Mapping[str, Any], name: str, default: str) -> str:
    value = raw.get(name, default)
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise OptionsError(f"{name} must be one line of text")
    return value.strip()


def load_options(raw: Mapping[str, Any]) -> HttpOptions:
    """Check `raw` and return the client's options."""
    base_url = raw.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        raise OptionsError("base_url must be an https:// URL")
    return HttpOptions(
        base_url=base_url.rstrip("/"),
        timeout_seconds=_number(raw, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS, 0.1, MAX_TIMEOUT_SECONDS),
        max_redirects=int(_number(raw, "max_redirects", DEFAULT_MAX_REDIRECTS, 0, MAX_REDIRECTS)),
        verify_tls=_flag(raw, "verify_tls", True),
        user_agent=_text(raw, "user_agent", DEFAULT_USER_AGENT),
    )
