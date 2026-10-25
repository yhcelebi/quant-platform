"""Backtest input: a close-only price series from the point-in-time history (ADR 0014, 6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from qp.core import Currency, InstrumentId, QPValidationError
from qp.data.market import MarketHistory

FX_VENUE = "TCMB"


@dataclass(frozen=True, slots=True)
class PriceSeries:
    instrument: InstrumentId
    currency: Currency
    days: list[date]
    closes: list[Decimal]
    known_times: list[datetime]  # bar timestamps: when each close became known
    source: str

    def __post_init__(self) -> None:
        if not (len(self.days) == len(self.closes) == len(self.known_times)):
            raise QPValidationError("series lengths differ")
        if any(c <= 0 for c in self.closes):
            raise QPValidationError("closes must be positive")
        if any(b <= a for a, b in zip(self.known_times, self.known_times[1:], strict=False)):
            raise QPValidationError("known times must increase")

    def window(self, start: date | None, end: date | None) -> PriceSeries:
        idx = [
            i
            for i, d in enumerate(self.days)
            if (start is None or d >= start) and (end is None or d <= end)
        ]
        return PriceSeries(
            self.instrument,
            self.currency,
            [self.days[i] for i in idx],
            [self.closes[i] for i in idx],
            [self.known_times[i] for i in idx],
            self.source,
        )

    @property
    def price_precision(self) -> int:
        exps = [-c.normalize().as_tuple().exponent for c in self.closes]  # type: ignore[operator]
        return max(0, min(8, max(exps, default=0)))


def price_series(
    market: MarketHistory, instrument: InstrumentId, *, end: date, as_of: datetime
) -> PriceSeries:
    """Every price of ``instrument`` known at ``as_of`` up to ``end``."""
    points = market.price_history(instrument, end, as_of=as_of)
    if not points:
        raise QPValidationError(f"no prices for {instrument} known at {as_of}")
    currency = points[0].currency
    if any(p.currency != currency for p in points):
        raise QPValidationError(f"{instrument}: prices in more than one currency")
    return PriceSeries(
        instrument,
        currency,
        [p.valid_date for p in points],
        [p.price for p in points],
        [p.known_time for p in points],
        ",".join(sorted({p.source for p in points})),
    )


def fx_series(
    market: MarketHistory, currency: Currency, *, end: date, as_of: datetime
) -> PriceSeries:
    """A currency's TRY rate as a tradable series ``<CODE>TRY.TCMB`` priced in TRY."""
    points = market.fx_history(currency, end, as_of=as_of)
    if not points:
        raise QPValidationError(f"no {currency}/TRY rates known at {as_of}")
    return PriceSeries(
        InstrumentId(f"{currency.code}TRY", FX_VENUE),
        Currency("TRY"),
        [p.valid_date for p in points],
        [p.rate_try for p in points],
        [p.known_time for p in points],
        ",".join(sorted({p.source for p in points})),
    )
