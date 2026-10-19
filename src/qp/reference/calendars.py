"""Trading calendars per venue, from ``exchange-calendars`` (ADR 0009).

Venue -> exchange calendar: BIST and TEFAS -> XIST, NYSE and ARCA -> XNYS, NASDAQ -> XNAS,
BINANCE -> 24/7. TCMB publishes indicative FX rates on XIST full days only: on the half days
before religious holidays (arife) there is no bulletin (checked against the TCMB archive,
2026-10-19: 28 Oct 2025, 19 Mar 2026 and 26 May 2026 have none).

Limitation: future holidays are those known to the installed library version, not point in
time. Dates outside the library's range raise :class:`CalendarError`; nothing is guessed.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import cache

import exchange_calendars as xc
import pandas as pd

from qp.core import QPError

CALENDAR_OF_VENUE: dict[str, str] = {
    "BIST": "XIST",
    "TEFAS": "XIST",
    "TCMB": "XIST",
    "NYSE": "XNYS",
    "ARCA": "XNYS",
    "NASDAQ": "XNAS",
    "BINANCE": "24/7",
}


_NO_HALF_DAYS = frozenset({"TCMB"})


class CalendarError(QPError):
    pass


@cache
def _calendar(venue: str) -> xc.ExchangeCalendar:
    name = CALENDAR_OF_VENUE.get(venue)
    if name is None:
        raise CalendarError(f"no trading calendar for venue {venue!r}")
    return xc.get_calendar(name)


def _check(venue: str, *days: date) -> xc.ExchangeCalendar:
    cal = _calendar(venue)
    first, last = cal.first_session.date(), cal.last_session.date()
    for d in days:
        if not first <= d <= last:
            raise CalendarError(
                f"{venue} calendar covers {first}..{last} (exchange-calendars "
                f"{xc.__version__}); {d} is outside it"
            )
    return cal


def coverage(venue: str) -> tuple[date, date]:
    cal = _calendar(venue)
    return cal.first_session.date(), cal.last_session.date()


def _half_day(cal: xc.ExchangeCalendar, ts: pd.Timestamp) -> bool:
    return bool(ts in cal.early_closes)


def is_session(venue: str, day: date) -> bool:
    cal = _check(venue, day)
    ts = pd.Timestamp(day)
    if not cal.is_session(ts):
        return False
    return not (venue in _NO_HALF_DAYS and _half_day(cal, ts))


def sessions(venue: str, start: date, end: date) -> list[date]:
    """Trading days in [start, end]."""
    if end < start:
        return []
    cal = _check(venue, start, end)
    days = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    if venue in _NO_HALF_DAYS:
        days = days.difference(cal.early_closes)
    return [ts.date() for ts in days]


def previous_session(venue: str, day: date) -> date:
    """The last trading day strictly before ``day``."""
    cal = _check(venue, day)
    ts = pd.Timestamp(day)
    prev = cal.previous_session(ts) if cal.is_session(ts) else cal.date_to_session(ts, "previous")
    while venue in _NO_HALF_DAYS and _half_day(cal, prev):
        prev = cal.previous_session(prev)
    return prev.date()  # type: ignore[no-any-return]


def session_close(venue: str, day: date) -> datetime:
    """Closing time (UTC) of a trading day, including half days."""
    cal = _check(venue, day)
    if not cal.is_session(pd.Timestamp(day)):
        raise CalendarError(f"{day} is not a {venue} trading day")
    close: datetime = cal.session_close(pd.Timestamp(day)).to_pydatetime()
    return close
