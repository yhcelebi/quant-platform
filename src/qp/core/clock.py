"""Clock abstraction: code asks a Clock for 'now' so tests can control time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from qp.core.time import ensure_utc


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current instant, tz-aware UTC."""
        ...


class SystemClock:
    """Wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """A clock that only moves when told to. For tests and replays."""

    def __init__(self, start: datetime) -> None:
        self._now = ensure_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = ensure_utc(value)

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("ManualClock cannot move backwards via advance(); use set()")
        self._now = self._now + delta
