import pytest

from duration import format_duration, parse_duration


def test_single_components():
    assert parse_duration("45s") == 45
    assert parse_duration("2d") == 172800


def test_combined():
    assert parse_duration("1h30m") == 5400


def test_garbage_is_rejected():
    with pytest.raises(ValueError):
        parse_duration("abc")


def test_format():
    assert format_duration(5400) == "1h30m"
    assert format_duration(45) == "45s"
