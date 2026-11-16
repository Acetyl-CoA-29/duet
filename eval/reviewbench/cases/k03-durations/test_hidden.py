import pytest

from module import add_durations, format_duration, parse_duration


def test_happy_format_parse_round_trip():
    assert format_duration(3723) == "1h 02m 03s" and format_duration(123) == "2m 03s"
    assert format_duration(3) == "3s" and format_duration(0) == "0s" and format_duration(3600) == "1h 00m 00s"
    for seconds in (0, 59, 60, 3599, 3600, 86399, 90061):
        assert parse_duration(format_duration(seconds)) == seconds
    assert parse_duration("90m") == 5400 and parse_duration(" 1H 5S ") == 3605
    assert add_durations("45m", "30m 30s") == "1h 15m 30s"
    for bad in ("", "  ", "5", "3s 2m", "1h2x", "-5s", "1.5h"):
        with pytest.raises(ValueError):
            parse_duration(bad)
    with pytest.raises(ValueError):
        format_duration(-1)
