"""Time conventions (rule 4): store tz-aware UTC, display in Europe/Istanbul.

A naive datetime is a bug; functions here raise NaiveDatetimeError instead of guessing.
The only exception is :func:`assume_istanbul`, which callers use explicitly to attach the
Istanbul zone to a naive local wall-clock time (e.g. from a broker CSV).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from qp.core.errors import NaiveDatetimeError

__all__ = [
    "ISTANBUL",
    "UTC",
    "assume_istanbul",
    "ensure_utc",
    "is_aware",
    "istanbul_date",
    "parse_utc",
    "to_istanbul",
]

ISTANBUL = ZoneInfo("Europe/Istanbul")


def is_aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` converted to UTC. Raises NaiveDatetimeError for naive input."""
    if not isinstance(dt, datetime):
        raise TypeError(f"expected datetime, got {type(dt).__name__}")
    if not is_aware(dt):
        raise NaiveDatetimeError(f"naive datetime is not allowed: {dt!r}")
    return dt.astimezone(UTC)


def to_istanbul(dt: datetime) -> datetime:
    """Convert an aware datetime to Europe/Istanbul for display."""
    return ensure_utc(dt).astimezone(ISTANBUL)


def istanbul_date(dt: datetime) -> date:
    """The Istanbul calendar date of an aware instant."""
    return to_istanbul(dt).date()


def assume_istanbul(local: datetime) -> datetime:
    """Attach Europe/Istanbul to a naive local wall-clock time and return it in UTC."""
    if is_aware(local):
        raise ValueError(f"datetime is already aware: {local!r}")
    return local.replace(tzinfo=ISTANBUL).astimezone(UTC)


def parse_utc(text: str) -> datetime:
    """Parse an ISO 8601 timestamp that carries an offset (or 'Z') and return it in UTC."""
    return ensure_utc(datetime.fromisoformat(text))
