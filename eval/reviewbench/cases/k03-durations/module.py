"""Format and parse short durations such as `1h 02m 03s`."""
import re
from typing import Optional

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
DURATION_PATTERN = re.compile(r"(?:(\d{1,6})h)?\s*(?:(\d{1,6})m)?\s*(?:(\d{1,6})s)?")


def check_seconds(seconds: int) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError("a duration is a whole number of seconds")
    if seconds < 0:
        raise ValueError("a duration cannot be negative")
    return seconds


def format_duration(seconds: int) -> str:
    """`1h 02m 03s`, `2m 03s` or `3s`: the largest unit is not padded, smaller ones are."""
    remaining = check_seconds(seconds)
    hours, remaining = divmod(remaining, SECONDS_PER_HOUR)
    minutes, secs = divmod(remaining, SECONDS_PER_MINUTE)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _part(text: Optional[str]) -> int:
    return int(text) if text is not None else 0


def parse_duration(text: str) -> int:
    """The number of seconds that `text` describes. At least one of the h, m and s parts must be
    present, in that order; nothing else may appear."""
    if not isinstance(text, str):
        raise ValueError("a duration must be given as text")
    match = DURATION_PATTERN.fullmatch(text.strip().lower())
    if match is None or all(group is None for group in match.groups()):
        raise ValueError(f"not a duration: {text!r}")
    hours, minutes, secs = (_part(group) for group in match.groups())
    return hours * SECONDS_PER_HOUR + minutes * SECONDS_PER_MINUTE + secs


def add_durations(first: str, second: str) -> str:
    """The sum of two durations, formatted."""
    return format_duration(parse_duration(first) + parse_duration(second))
