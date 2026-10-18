"""Point-in-time market data interface (rule 5).

Phase 1 implements it from owner-maintained CSV files (:mod:`qp.data.manual`); phase 2 adds
a lake-backed implementation behind the same interface.
"""

from __future__ import annotations

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


def try_rate(
    market: MarketData, currency: Currency, on: date, *, as_of: datetime
) -> FxPoint | None:
    """FX point for ``currency``; TRY itself is 1 by definition."""
    if currency == TRY:
        return FxPoint(TRY, on, Decimal(1), "identity", as_of)
    return market.fx_try(currency, on, as_of=as_of)
