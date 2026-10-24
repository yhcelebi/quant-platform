"""Benchmarks as growth indices on the portfolio's valuation days (ADR 0010, 11).

- ``fx``: holding a foreign currency; level = TCMB rate (TRY per unit).
- ``price``: an instrument (BIST 100, gold, S&P 500 TR); level = price x its currency's rate.
- ``rate``: a deposit rate series (simple annual, ``value x scale`` as a fraction);
  ``B_d = B_prev x (1 + r x days / 365)`` with ``r`` the latest rate on or before the
  previous day.

Levels are in TRY; the USD and real views convert them like portfolio values.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from qp.core import Currency, InstrumentId, QPValidationError
from qp.data.market import MarketData, try_rate
from qp.data.series import SeriesData
from qp.performance.metrics import InflationIndex, Unavailable, View

ONE = Decimal(1)


class BenchmarkKind(StrEnum):
    FX = "fx"
    PRICE = "price"
    RATE = "rate"


@dataclass(frozen=True, slots=True)
class BenchmarkSpec:
    name: str
    kind: BenchmarkKind
    currency: str | None = None  # fx
    instrument: str | None = None  # price
    series: str | None = None  # rate
    scale: Decimal = ONE  # rate: 0.01 for a series in percent

    def __post_init__(self) -> None:
        needed = {
            BenchmarkKind.FX: self.currency,
            BenchmarkKind.PRICE: self.instrument,
            BenchmarkKind.RATE: self.series,
        }[self.kind]
        if not needed:
            raise QPValidationError(f"benchmark {self.name}: {self.kind} needs its source field")


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    spec: BenchmarkSpec
    total: Decimal  # return over the window in the view
    levels: list[tuple[date, Decimal]]  # (day, level normalized to 1 at start)


def levels_try(
    spec: BenchmarkSpec,
    dates: Sequence[date],
    market: MarketData,
    series: SeriesData,
    *,
    as_of: datetime,
) -> list[Decimal] | Unavailable:
    """TRY level of the benchmark on each of ``dates`` (ascending)."""
    if not dates:
        return Unavailable("no days")
    out: list[Decimal] = []
    if spec.kind is BenchmarkKind.FX:
        assert spec.currency is not None
        cur = Currency(spec.currency)
        for d in dates:
            point = try_rate(market, cur, d, as_of=as_of)
            if point is None:
                return Unavailable(f"{spec.name}: no {cur}/TRY rate on or before {d}")
            out.append(point.rate_try)
        return out
    if spec.kind is BenchmarkKind.PRICE:
        assert spec.instrument is not None
        inst = InstrumentId.parse(spec.instrument)
        for d in dates:
            price = market.price(inst, d, as_of=as_of)
            if price is None:
                return Unavailable(f"{spec.name}: no {inst} price on or before {d}")
            fx = try_rate(market, price.currency, d, as_of=as_of)
            if fx is None:
                return Unavailable(f"{spec.name}: no {price.currency}/TRY rate on or before {d}")
            out.append(price.price * fx.rate_try)
        return out
    assert spec.series is not None
    history = series.history(spec.series, dates[-1], as_of=as_of)
    level = ONE
    out.append(level)
    for prev, cur_day in pairwise(dates):
        known = [p for p in history if p.valid_date <= prev]
        if not known:
            return Unavailable(f"{spec.name}: no {spec.series} rate on or before {prev}")
        rate = known[-1].value * spec.scale
        level *= ONE + rate * Decimal((cur_day - prev).days) / Decimal(365)
        out.append(level)
    return out


def benchmark(
    spec: BenchmarkSpec,
    dates: Sequence[date],
    view: View,
    market: MarketData,
    series: SeriesData,
    *,
    as_of: datetime,
    usd_try: Callable[[date], Decimal | None] | None = None,
    index: InflationIndex | None = None,
) -> BenchmarkResult | Unavailable:
    """The benchmark's return and normalized levels over ``dates`` in ``view``."""
    raw = levels_try(spec, dates, market, series, as_of=as_of)
    if isinstance(raw, Unavailable):
        return raw
    converted: list[Decimal] = []
    for d, level in zip(dates, raw, strict=True):
        if view is View.TRY:
            converted.append(level)
        elif view is View.USD:
            rate = usd_try(d) if usd_try else None
            if rate is None:
                return Unavailable(f"{spec.name}: no USD/TRY rate on {d}")
            converted.append(level / rate)
        else:
            i = index.level(d) if index else None
            if i is None:
                return Unavailable(f"{spec.name}: no inflation index level on {d}")
            converted.append(level / i)
    base = converted[0]
    if base <= 0:
        return Unavailable(f"{spec.name}: non-positive starting level")
    normalized = [x / base for x in converted]
    return BenchmarkResult(spec, normalized[-1] - ONE, list(zip(dates, normalized, strict=True)))
