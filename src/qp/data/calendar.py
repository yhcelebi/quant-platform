"""Economic calendar facts (ADR 0012, 5): release dates, point in time.

Lake dataset ``calendar_events``: key ``(source, event)`` (e.g. ``fred``, ``release:10``),
valid date = the scheduled day; ``title``, ``time``, ``country`` and ``category`` are the
compared values. A moved release appears under its new day; both stay visible with the
time each was learned. The owner's ``data/manual/calendar.csv``
(``day,event,title,time,country,category,source,known_time``; ``known_time`` required)
covers what has no machine-readable source (TÜİK, TCMB MPC).
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

from qp.core import QPError, parse_utc
from qp.data.lake import CleanStore, Dataset, Row

DATASET = Dataset(
    name="calendar_events",
    keys=("calendar", "event"),
    values=("title", "time", "country", "category"),
    fields=(
        pa.field("calendar", pa.string(), nullable=False),
        pa.field("event", pa.string(), nullable=False),
        pa.field("valid_date", pa.date32(), nullable=False),
        pa.field("title", pa.string(), nullable=False),
        pa.field("time", pa.string(), nullable=False),  # "" when only the day is known
        pa.field("country", pa.string(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
    ),
)
MANUAL_COLUMNS = ("day", "event", "title", "time", "country", "category", "source", "known_time")


class CalendarError(QPError):
    pass


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    calendar: str  # fred | manual
    event: str
    day: date
    title: str
    time: str
    country: str
    category: str
    source: str
    known_time: datetime


def from_row(row: Row) -> CalendarEvent:
    return CalendarEvent(
        row["calendar"],
        row["event"],
        row["valid_date"],
        row["title"],
        row["time"],
        row["country"],
        row["category"],
        row["source"],
        row["known_time"],
    )


def lake_events(
    clean: CleanStore, start: date, end: date, *, as_of: datetime
) -> list[CalendarEvent]:
    rows = clean.query(DATASET, as_of=as_of, valid_from=start, valid_to=end)
    return [from_row(r) for r in rows]


def manual_events(path: Path) -> list[CalendarEvent]:
    """All rows of calendar.csv (a missing file is empty); filter by ``known_time`` later."""
    if not path.is_file():
        return []
    out: list[CalendarEvent] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != MANUAL_COLUMNS:
            raise CalendarError(f"{path}: header must be {list(MANUAL_COLUMNS)}")
        for line, raw in enumerate(reader, start=2):
            try:
                if None in raw or None in raw.values():
                    raise ValueError("field count differs from the header")
                v = {k: x.strip() for k, x in raw.items()}
                if not v["known_time"]:
                    raise ValueError("known_time is required (when was the date announced?)")
                if not v["event"] or not v["title"]:
                    raise ValueError("event and title are required")
                out.append(
                    CalendarEvent(
                        "manual",
                        v["event"],
                        date.fromisoformat(v["day"]),
                        v["title"],
                        v["time"],
                        v["country"],
                        v["category"],
                        v["source"] or "manual",
                        parse_utc(v["known_time"]),
                    )
                )
            except (QPError, ValueError) as exc:
                raise CalendarError(f"{path}: line {line}: {exc}") from exc
    return out


def known_manual(
    events: Sequence[CalendarEvent], start: date, end: date, *, as_of: datetime
) -> list[CalendarEvent]:
    """Per (event, day), the latest row known at ``as_of`` inside [start, end]."""
    best: dict[tuple[str, date], CalendarEvent] = {}
    for e in sorted(events, key=lambda e: e.known_time):
        if start <= e.day <= end and e.known_time <= as_of:
            best[(e.event, e.day)] = e
    return list(best.values())
