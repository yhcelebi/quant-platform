"""Point-in-time market data interface (rule 5).

Phase 1 implements it from owner-maintained CSV files (:mod:`qp.data.manual`); phase 2 adds
a lake-backed implementation behind the same interface. Phase 3 adds histories
(:class:`MarketHistory`) and :class:`PreloadedMarketData`, which loads each history once and
answers daily point queries from memory.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from qp.core import TRY, Currency, InstrumentId


@dataclass(frozen=True, slots=True)
class PricePoint:
    instrument: InstrumentId
    valid_date: date
    price: Decimal
    currency: Currency
    source: str
    known_time: datetime


@dataclass(frozen=True, slots=True)
class FxPoint:
    """``rate_try`` TRY per 1 unit of ``currency`` (e.g. USD 41.20)."""

    currency: Currency
    valid_date: date
    rate_try: Decimal
    source: str
    known_time: datetime


class MarketData(Protocol):
    def price(self, instrument: InstrumentId, on: date, *, as_of: datetime) -> PricePoint | None:
        """Latest price with valid_date <= on, among facts with known_time <= as_of."""
        ...

    def fx_try(self, currency: Currency, on: date, *, as_of: datetime) -> FxPoint | None:
        """Latest TRY rate with valid_date <= on, among facts with known_time <= as_of."""
        ...


class MarketHistory(MarketData, Protocol):
    """Whole histories: one point per valid date (its latest revision known at ``as_of``),
    ascending, up to and including ``until``."""

    def price_history(
        self, instrument: InstrumentId, until: date, *, as_of: datetime
    ) -> list[PricePoint]: ...

    def fx_history(self, currency: Currency, until: date, *, as_of: datetime) -> list[FxPoint]: ...


def try_rate(
    market: MarketData, currency: Currency, on: date, *, as_of: datetime
) -> FxPoint | None:
    """FX point for ``currency``; TRY itself is 1 by definition."""
    if currency == TRY:
        return FxPoint(TRY, on, Decimal(1), "identity", as_of)
    return market.fx_try(currency, on, as_of=as_of)


class LayeredMarketData:
    """Several sources behind one interface: the point with the latest valid date wins, and
    on the same date the earlier source wins (manual entries before fetched data)."""

    def __init__(self, sources: Sequence[MarketHistory]) -> None:
        self.sources = list(sources)

    def price(self, instrument: InstrumentId, on: date, *, as_of: datetime) -> PricePoint | None:
        points = [s.price(instrument, on, as_of=as_of) for s in self.sources]
        return _newest([p for p in points if p is not None])

    def fx_try(self, currency: Currency, on: date, *, as_of: datetime) -> FxPoint | None:
        points = [s.fx_try(currency, on, as_of=as_of) for s in self.sources]
        return _newest([p for p in points if p is not None])

    def price_history(
        self, instrument: InstrumentId, until: date, *, as_of: datetime
    ) -> list[PricePoint]:
        return _merge([s.price_history(instrument, until, as_of=as_of) for s in self.sources])

    def fx_history(self, currency: Currency, until: date, *, as_of: datetime) -> list[FxPoint]:
        return _merge([s.fx_history(currency, until, as_of=as_of) for s in self.sources])


def _merge[P: (PricePoint, FxPoint)](histories: list[list[P]]) -> list[P]:
    """One point per date; on the same date the earlier source wins (as in point queries)."""
    by_date: dict[date, P] = {}
    for history in histories:
        for p in history:
            by_date.setdefault(p.valid_date, p)
    return [by_date[d] for d in sorted(by_date)]


def _newest[P: (PricePoint, FxPoint)](points: list[P]) -> P | None:
    best: P | None = None
    for p in points:  # strict > keeps the earlier source on ties
        if best is None or p.valid_date > best.valid_date:
            best = p
    return best


def latest_on[P: (PricePoint, FxPoint)](history: Sequence[P], on: date) -> P | None:
    """The point with the greatest valid date <= ``on`` in an ascending history."""
    i = bisect_right([p.valid_date for p in history], on) if history else 0
    return history[i - 1] if i else None


class PreloadedMarketData:
    """Point queries answered from histories loaded once per instrument or currency.

    Bound to one ``as_of`` and an ``until`` date: asking about a later day or another
    ``as_of`` is an error, so a preloaded view can never leak facts it was not built for.
    """

    def __init__(self, source: MarketHistory, *, as_of: datetime, until: date) -> None:
        self.source = source
        self.as_of = as_of
        self.until = until
        self._prices: dict[InstrumentId, tuple[list[PricePoint], list[date]]] = {}
        self._fx: dict[Currency, tuple[list[FxPoint], list[date]]] = {}

    def _check(self, on: date, as_of: datetime) -> None:
        if as_of != self.as_of:
            raise ValueError(f"preloaded for as_of {self.as_of}, asked for {as_of}")
        if on > self.until:
            raise ValueError(f"preloaded until {self.until}, asked for {on}")

    def price_history(
        self, instrument: InstrumentId, until: date, *, as_of: datetime
    ) -> list[PricePoint]:
        self._check(until, as_of)
        if instrument not in self._prices:
            h = self.source.price_history(instrument, self.until, as_of=self.as_of)
            self._prices[instrument] = (h, [p.valid_date for p in h])
        points, dates = self._prices[instrument]
        return points[: bisect_right(dates, until)]

    def fx_history(self, currency: Currency, until: date, *, as_of: datetime) -> list[FxPoint]:
        self._check(until, as_of)
        if currency not in self._fx:
            h = self.source.fx_history(currency, self.until, as_of=self.as_of)
            self._fx[currency] = (h, [p.valid_date for p in h])
        points, dates = self._fx[currency]
        return points[: bisect_right(dates, until)]

    def price(self, instrument: InstrumentId, on: date, *, as_of: datetime) -> PricePoint | None:
        history = self.price_history(instrument, on, as_of=as_of)
        return history[-1] if history else None

    def fx_try(self, currency: Currency, on: date, *, as_of: datetime) -> FxPoint | None:
        history = self.fx_history(currency, on, as_of=as_of)
        return history[-1] if history else None
