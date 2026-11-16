from datetime import datetime

import pytest

from module import DeadlineError, check_deadline, describe, parse_deadline

NOW = datetime(2024, 3, 1, 4, 0, 0)


def test_happy_utc_text():
    deadline = parse_deadline("2024-03-01T06:30:00")
    assert deadline == datetime(2024, 3, 1, 6, 30) and describe(deadline, NOW) == "due in 2h 30m"
    assert parse_deadline("2024-03-01T06:30:00+00:00") == deadline
    with pytest.raises(DeadlineError):
        check_deadline(parse_deadline("2024-02-29T23:00:00"), NOW)


def test_defect_offset_is_converted_to_utc():
    assert parse_deadline("2024-03-01T10:00:00+05:00") == datetime(2024, 3, 1, 5, 0)
    assert describe(parse_deadline("2024-03-01T03:00:00-02:00"), NOW) == "due in 1h 00m"
