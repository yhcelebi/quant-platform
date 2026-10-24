"""Market data from the lake behind the :class:`~qp.data.market.MarketData` interface.

FX: TCMB indicative rates of the configured kind (default döviz alış), per 1 unit of the
currency. Prices: no lake price source yet (phase 2 open question), so ``price`` returns None
and valuation falls back to the manual table via :class:`~qp.data.market.LayeredMarketData`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from qp.core import Currency, InstrumentId
from qp.data.connectors import tcmb
from qp.data.lake import CleanStore, Row
from qp.data.market import FxPoint, PricePoint


def plain(value: Decimal) -> Decimal:
    """Drop the storage scale's trailing zeros without changing the value (49.3573, not
    49.357300000000000000; 100, not 1E+2)."""
    normalized = value.normalize()
    return normalized.quantize(Decimal(1)) if normalized.as_tuple().exponent > 0 else normalized  # type: ignore[operator]


class LakeMarketData:
    def __init__(self, clean: CleanStore, *, rate_kind: str = "forex_buying") -> None:
        self.clean = clean
        self.rate_kind = rate_kind

    def price(self, instrument: InstrumentId, on: date, *, as_of: datetime) -> PricePoint | None:
        return None

    def price_history(
        self, instrument: InstrumentId, until: date, *, as_of: datetime
    ) -> list[PricePoint]:
        return []

    def fx_try(self, currency: Currency, on: date, *, as_of: datetime) -> FxPoint | None:
        row = self.clean.latest_on(
            tcmb.DATASET,
            {"currency": currency.code, "rate_kind": self.rate_kind},
            on,
            as_of=as_of,
        )
        return None if row is None else self._fx_point(currency, row)

    def fx_history(self, currency: Currency, until: date, *, as_of: datetime) -> list[FxPoint]:
        rows = self.clean.query(
            tcmb.DATASET,
            as_of=as_of,
            where={"currency": currency.code, "rate_kind": self.rate_kind},
            valid_to=until,
        )
        return [self._fx_point(currency, r) for r in rows]

    def _fx_point(self, currency: Currency, row: Row) -> FxPoint:
        rate = plain(Decimal(row["value"]) / Decimal(row["unit"]))
        return FxPoint(
            currency,
            row["valid_date"],
            rate,
            f"{tcmb.SOURCE}:{self.rate_kind}",
            row["known_time"],
        )
