"""Durations such as "1h30m" for job configuration."""
import re

UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
PATTERN = re.compile(r"(?:([0-9]+)d)?(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?")


def parse_duration(text):
    if not isinstance(text, str):
        raise TypeError("duration must be a str")
    stripped = text.strip()
    match = PATTERN.fullmatch(stripped)
    if not stripped or match is None:
        raise ValueError(f"invalid duration: {text!r}")
    return sum(int(number) * size for number, (_, size) in zip(match.groups(), UNITS) if number is not None)


def format_duration(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise TypeError("seconds must be an int")
    if seconds < 0:
        raise ValueError("seconds must not be negative")
    parts = []
    for unit, size in UNITS:
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts) or "0s"
