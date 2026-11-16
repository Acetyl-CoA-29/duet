"""Verify that an incoming webhook was signed with the shared secret."""
import hashlib
import hmac
import json
from typing import Any, Dict, Mapping

SIGNATURE_HEADER = "X-Signature"
SCHEME = "sha256="
MAX_AGE_SECONDS = 300


class WebhookError(Exception):
    """The webhook is refused."""


def sign(secret: bytes, body: bytes) -> str:
    """The header value a genuine sender attaches to `body`."""
    return SCHEME + hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, body: bytes, header_value: str) -> bool:
    """True only when `header_value` is the signature of `body` under `secret`."""
    if not secret:
        raise ValueError("a secret is required")
    expected = sign(secret, body)
    given = header_value.strip().lower()
    return hmac.compare_digest(expected, given)


def check_age(event: Mapping[str, Any], now: float) -> None:
    sent_at = event.get("sent_at")
    if isinstance(sent_at, bool) or not isinstance(sent_at, (int, float)):
        raise WebhookError("the event has no usable sent_at time")
    if sent_at > now + MAX_AGE_SECONDS:
        raise WebhookError("the event is dated in the future")
    if now - sent_at > MAX_AGE_SECONDS:
        raise WebhookError("the event is too old")


def parse_event(secret: bytes, headers: Mapping[str, str], body: bytes, now: float) -> Dict[str, Any]:
    """Return the event carried by a webhook, or raise WebhookError when it must be refused."""
    if not verify_signature(secret, body, headers.get(SIGNATURE_HEADER, "")):
        raise WebhookError("the signature does not match")
    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookError(f"the body is not valid JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise WebhookError("the body must be a JSON object")
    check_age(event, now)
    return event
