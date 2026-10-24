"""Point-in-time numeric series: price indices (TÜFE), interest rates, macro levels.

A series id names one series: ``evds:CODE`` and ``fred:ID`` are read from the lake; any id
can also be given by the owner in ``data/manual/series.csv``
(``valid_date,series,value,source,known_time``). ``known_time`` is required there: a monthly
index is published weeks after its month, so no default would be honest.

``valid_date`` is the first day of the period for monthly or lower-frequency series (as EVDS
and FRED report them).
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from qp.core import QPError, parse_utc, to_decimal
from qp.data.connectors import evds, fred
from qp.data.lake import CleanStore, Dataset

COLUMNS = ("valid_date", "series", "value", "source", "known_time")


class SeriesError(QPError):
    pass


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    series: str
    valid_date: date
    value: Decimal
    source: str
    known_time: datetime


class SeriesData(Protocol):
    def history(self, series: str, until: date, *, as_of: datetime) -> list[SeriesPoint]:
        """One point per valid date (latest revision known at ``as_of``), ascending."""
        ...


class ManualSeries:
    def __init__(self, points: Sequence[SeriesPoint]) -> None:
        seen: set[tuple[str, date, datetime]] = set()
        for p in points:
            key = (p.series, p.valid_date, p.known_time)
            if key in seen:
                raise SeriesError(f"duplicate series row {p.series} {p.valid_date} {p.known_time}")
            seen.add(key)
        self._points = sorted(points, key=lambda p: (p.series, p.valid_date, p.known_time))

    @classmethod
    def from_file(cls, path: Path) -> ManualSeries:
        """Load ``series.csv``; a missing file is an empty source."""
        if not path.is_file():
            return cls([])
        points: list[SeriesPoint] = []
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if tuple(reader.fieldnames or ()) != COLUMNS:
                raise SeriesError(f"{path}: header must be {list(COLUMNS)}")
            for line, raw in enumerate(reader, start=2):
                try:
                    if None in raw or None in raw.values():
                        raise ValueError("field count differs from the header")
                    if not raw["known_time"].strip():
                        raise ValueError("known_time is required (when was it published?)")
                    points.append(
                        SeriesPoint(
                            raw["series"].strip(),
                            date.fromisoformat(raw["valid_date"].strip()),
                            to_decimal(raw["value"]),
                            raw["source"].strip() or "manual",
                            parse_utc(raw["known_time"].strip()),
                        )
                    )
                except (QPError, ValueError) as exc:
                    raise SeriesError(f"{path}: line {line}: {exc}") from exc
        return cls(points)

    def history(self, series: str, until: date, *, as_of: datetime) -> list[SeriesPoint]:
        by_date: dict[date, SeriesPoint] = {}
        for p in self._points:  # sorted: later revisions of a date overwrite earlier ones
            if p.series == series and p.valid_date <= until and p.known_time <= as_of:
                by_date[p.valid_date] = p
        return [by_date[d] for d in sorted(by_date)]


_LAKE: dict[str, tuple[Dataset, str]] = {
    "evds": (evds.DATASET, "series_code"),
    "fred": (fred.DATASET, "series_id"),
}


class LakeSeries:
    """``evds:CODE`` and ``fred:ID`` from the clean lake; other ids have no lake history."""

    def __init__(self, clean: CleanStore) -> None:
        self.clean = clean

    def history(self, series: str, until: date, *, as_of: datetime) -> list[SeriesPoint]:
        prefix, _, code = series.partition(":")
        if prefix not in _LAKE or not code:
            return []
        dataset, key = _LAKE[prefix]
        rows = self.clean.query(dataset, as_of=as_of, where={key: code}, valid_to=until)
        return [
            SeriesPoint(series, r["valid_date"], r["value"], r["source"], r["known_time"])
            for r in rows
        ]


class LayeredSeries:
    """Earlier sources win on the same valid date (manual before the lake)."""

    def __init__(self, sources: Sequence[SeriesData]) -> None:
        self.sources = list(sources)

    def history(self, series: str, until: date, *, as_of: datetime) -> list[SeriesPoint]:
        by_date: dict[date, SeriesPoint] = {}
        for source in self.sources:
            for p in source.history(series, until, as_of=as_of):
                by_date.setdefault(p.valid_date, p)
        return [by_date[d] for d in sorted(by_date)]
