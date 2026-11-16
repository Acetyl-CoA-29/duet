"""Deadlines for submitted jobs. Internally every time is a naive datetime in UTC."""
from datetime import datetime, timedelta, timezone
from typing import Optional

MAX_HORIZON = timedelta(days=30)


class DeadlineError(ValueError):
    """The deadline cannot be accepted."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_deadline(text: str) -> datetime:
    """Parse an ISO 8601 timestamp such as 2024-03-01T10:00:00+05:00 into the internal form.
    A timestamp without an offset is taken to be UTC already."""
    try:
        parsed = datetime.fromisoformat(text.strip())
    except (ValueError, AttributeError) as exc:
        raise DeadlineError(f"not an ISO 8601 timestamp: {text!r}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(tzinfo=None)


def check_deadline(deadline: datetime, now: Optional[datetime] = None) -> datetime:
    """Accept a deadline that lies in the future but no further ahead than MAX_HORIZON."""
    current = utc_now() if now is None else now
    if deadline <= current:
        raise DeadlineError("the deadline has already passed")
    if deadline - current > MAX_HORIZON:
        raise DeadlineError(f"the deadline is more than {MAX_HORIZON.days} days away")
    return deadline


def seconds_left(deadline: datetime, now: Optional[datetime] = None) -> float:
    current = utc_now() if now is None else now
    return max(0.0, (deadline - current).total_seconds())


def is_overdue(deadline: datetime, now: Optional[datetime] = None) -> bool:
    current = utc_now() if now is None else now
    return current >= deadline


def describe(deadline: datetime, now: Optional[datetime] = None) -> str:
    """A short status line for the job list."""
    if is_overdue(deadline, now):
        return "overdue"
    minutes = int(seconds_left(deadline, now) // 60)
    return f"due in {minutes // 60}h {minutes % 60:02d}m"
