import json

import pytest

from module import SIGNATURE_HEADER, WebhookError, parse_event, sign, verify_signature

SECRET = b"shared-secret-for-tests"
BODY = json.dumps({"type": "order.paid", "sent_at": 1000}).encode()


def test_happy_signed_event_and_wrong_signature():
    event = parse_event(SECRET, {SIGNATURE_HEADER: sign(SECRET, BODY)}, BODY, now=1010)
    assert event["type"] == "order.paid"
    with pytest.raises(WebhookError):
        parse_event(SECRET, {SIGNATURE_HEADER: sign(b"other-secret", BODY)}, BODY, now=1010)
    with pytest.raises(WebhookError):
        parse_event(SECRET, {SIGNATURE_HEADER: sign(SECRET, BODY)}, BODY, now=5000)


@pytest.mark.parametrize("header", ["", "sha256=", "sha"])
def test_defect_partial_signature_is_refused(header):
    assert verify_signature(SECRET, BODY, header) is False


def test_defect_missing_header_is_refused():
    with pytest.raises(WebhookError):
        parse_event(SECRET, {}, BODY, now=1010)
