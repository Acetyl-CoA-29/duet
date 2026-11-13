"""Durations such as "1h30m" for job configuration."""
import re

UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    parts = re.findall(r"(\d+)([dhms])", text)
    if not parts:
        raise ValueError(f"invalid duration: {text!r}")
    return sum(int(number) * UNITS[unit] for number, unit in parts)


def format_duration(seconds):
    out = ""
    for unit, size in UNITS.items():
        count, seconds = divmod(seconds, size)
        if count:
            out += f"{count}{unit}"
    return out
