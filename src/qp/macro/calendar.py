"""Economic calendar view: lake events (FRED release dates) and the owner's calendar.csv,
as known at ``as_of`` (ADR 0012, 5)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from qp.data.calendar import CalendarEvent, known_manual, lake_events, manual_events
from qp.data.lake import CleanStore


def events(
    clean: CleanStore,
    manual_file: Path,
    start: date,
    end: date,
    *,
    as_of: datetime,
    countries: set[str] | None = None,
) -> list[CalendarEvent]:
    """Events scheduled in [start, end], known at ``as_of``, by day then time then title."""
    out = lake_events(clean, start, end, as_of=as_of)
    out += known_manual(manual_events(manual_file), start, end, as_of=as_of)
    if countries:
        out = [e for e in out if e.country in countries]
    return sorted(out, key=lambda e: (e.day, e.time or "99:99", e.title))
