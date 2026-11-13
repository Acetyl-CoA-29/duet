import pytest

from duration import format_duration, parse_duration


@pytest.mark.parametrize("text,seconds", [
    ("45s", 45), ("1h30m", 5400), ("2d4h", 187200), ("0s", 0), ("90m", 5400),
    ("1d1h1m1s", 90061), ("  10m  ", 600), ("0d0s", 0), ("007s", 7),
])
def test_valid(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", [
    "", "   ", "abc", "90", "h", "1h foo", "foo1h", "1h 30m", "30m1h", "1h1h", "1s1s",
    "1.5h", "-5m", "+5m", "1H", "1w", "1h30", "1hm", "١h", "1h\n30m", "5 m",
])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)


@pytest.mark.parametrize("value", [None, 90, 1.5, b"1h"])
def test_parse_wrong_type(value):
    with pytest.raises(TypeError):
        parse_duration(value)


@pytest.mark.parametrize("seconds,text", [
    (0, "0s"), (45, "45s"), (60, "1m"), (5400, "1h30m"), (86400, "1d"),
    (90061, "1d1h1m1s"), (3601, "1h1s"), (172800 + 5, "2d5s"),
])
def test_format(seconds, text):
    assert format_duration(seconds) == text


def test_format_rejects_negative():
    with pytest.raises(ValueError):
        format_duration(-1)


@pytest.mark.parametrize("value", [1.0, "60", None, True])
def test_format_wrong_type(value):
    with pytest.raises(TypeError):
        format_duration(value)


@pytest.mark.parametrize("seconds", [0, 1, 59, 60, 3599, 3600, 86399, 86400, 1234567])
def test_round_trip(seconds):
    assert parse_duration(format_duration(seconds)) == seconds
