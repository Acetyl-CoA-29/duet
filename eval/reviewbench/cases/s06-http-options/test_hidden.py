import pytest

from module import OptionsError, load_options


def test_happy_explicit_options():
    options = load_options({"base_url": "https://api.example.test/", "timeout_seconds": 3, "verify_tls": True})
    assert options.base_url == "https://api.example.test" and options.timeout_seconds == 3.0
    assert options.verify_tls is True and options.max_redirects == 5
    with pytest.raises(OptionsError):
        load_options({"base_url": "ftp://example.test"})


def test_defect_certificates_are_checked_by_default():
    assert load_options({"base_url": "https://api.example.test"}).verify_tls is True
