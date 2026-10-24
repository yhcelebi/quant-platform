"""Yield curves from tenor series (ADR 0012, 4): per tenor the latest value on or before the
day, known at ``as_of``; linear interpolation between tenors, no extrapolation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise

from qp.data.series import SeriesData

# (label, years, series id), shortest first
US_TREASURY: tuple[tuple[str, float, str], ...] = (
    ("1M", 1 / 12, "fred:DGS1MO"),
    ("3M", 0.25, "fred:DGS3MO"),
    ("6M", 0.5, "fred:DGS6MO"),
    ("1Y", 1.0, "fred:DGS1"),
    ("2Y", 2.0, "fred:DGS2"),
    ("3Y", 3.0, "fred:DGS3"),
    ("5Y", 5.0, "fred:DGS5"),
    ("7Y", 7.0, "fred:DGS7"),
    ("10Y", 10.0, "fred:DGS10"),
    ("20Y", 20.0, "fred:DGS20"),
    ("30Y", 30.0, "fred:DGS30"),
)


@dataclass(frozen=True, slots=True)
class CurvePoint:
    label: str
    years: float
    value: Decimal  # percent
    valid_date: date
    stale: bool


@dataclass
class Curve:
    day: date
    points: list[CurvePoint] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def at(self, years: float) -> float | None:
        """Linear interpolation in years; None outside the known tenors."""
        pts = sorted(self.points, key=lambda p: p.years)
        for lo, hi in pairwise(pts):
            if lo.years <= years <= hi.years:
                w = (years - lo.years) / (hi.years - lo.years)
                return float(lo.value) + w * (float(hi.value) - float(lo.value))
        if len(pts) == 1 and pts[0].years == years:
            return float(pts[0].value)
        return None

    def spread(self, long: str, short: str) -> Decimal | None:
        """long - short in percentage points, from observed tenors only."""
        by = {p.label: p.value for p in self.points}
        if long in by and short in by:
            return by[long] - by[short]
        return None


def curve(
    tenors: Sequence[tuple[str, float, str]],
    series: SeriesData,
    day: date,
    *,
    as_of: datetime,
    stale_after_days: int = 5,
) -> Curve:
    c = Curve(day)
    for label, years, series_id in tenors:
        history = series.history(series_id, day, as_of=as_of)
        if not history:
            c.missing.append(label)
            continue
        last = history[-1]
        stale = (day - last.valid_date).days > stale_after_days
        c.points.append(CurvePoint(label, years, last.value, last.valid_date, stale))
    return c
