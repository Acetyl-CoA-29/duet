"""Helpers for closed whole-number intervals, such as booked time slots in minutes."""
from typing import Iterable, List, Sequence, Tuple

Interval = Tuple[int, int]


def check_interval(interval: Sequence[int]) -> Interval:
    """Return the interval as a (start, end) pair, or raise ValueError when it is not one."""
    if len(interval) != 2:
        raise ValueError("an interval is a (start, end) pair")
    start, end = interval
    for bound in (start, end):
        if isinstance(bound, bool) or not isinstance(bound, int):
            raise ValueError("interval bounds must be whole numbers")
    if start > end:
        raise ValueError(f"interval starts after it ends: ({start}, {end})")
    return (start, end)


def merge(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Sorted intervals covering the same points, with overlapping or touching ones joined."""
    ordered = sorted(check_interval(interval) for interval in intervals)
    merged: List[Interval] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged = [*merged[:-1], (merged[-1][0], max(merged[-1][1], end))]
        else:
            merged = [*merged, (start, end)]
    return merged


def covered_length(intervals: Iterable[Sequence[int]]) -> int:
    """Total length covered, counting overlapping stretches once."""
    return sum(end - start for start, end in merge(intervals))


def contains(intervals: Iterable[Sequence[int]], point: int) -> bool:
    return any(start <= point <= end for start, end in merge(intervals))


def gaps(intervals: Iterable[Sequence[int]], low: int, high: int) -> List[Interval]:
    """The stretches of [low, high] that no interval covers, in order."""
    if low > high:
        raise ValueError("low must not be greater than high")
    free: List[Interval] = []
    cursor = low
    for start, end in merge(intervals):
        if end < low or start > high:
            continue
        if start > cursor:
            free = [*free, (cursor, start)]
        cursor = max(cursor, end)
    if cursor < high:
        free = [*free, (cursor, high)]
    return free
