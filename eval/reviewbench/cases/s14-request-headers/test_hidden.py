import pytest

from module import build_headers, redact


def test_happy_headers_for_one_request():
    headers = build_headers(token="tok-1", extra={"X-Request-Id": "r-9"})
    assert headers["Authorization"] == "Bearer tok-1" and headers["X-Request-Id"] == "r-9"
    assert headers["Accept"] == "application/json"
    assert redact(headers)["Authorization"] == "***"
    with pytest.raises(ValueError):
        build_headers(extra={"Host": "evil.example.test"})


def test_defect_one_request_does_not_leak_into_the_next():
    build_headers(token="tok-for-first-caller", extra={"X-Tenant": "acme"})
    later = build_headers()
    assert "Authorization" not in later and "X-Tenant" not in later
