"""Holdings report: positions and cash at a date, valued in TRY and USD.

Point-in-time: with ``known_at`` the ledger is replayed as recorded by then and market data
is limited to facts known by then, so a past report can be reproduced exactly. Without it
(the default) every recorded fact is used. ``known_at`` is never defaulted to the local
clock: ``recorded_at`` comes from the database clock, and mixing clocks would hide
just-recorded entries.
Missing or stale prices are flagged; values are never estimated.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from qp.core import USD, Currency, ensure_utc
from qp.data.market import FxPoint, MarketData, PricePoint, try_rate
from qp.ledger.domain import Transaction
from qp.ledger.engine import IncomeEvent, Realization, replay
from qp.portfolio.valuation import PositionValuation, value_position

ZERO = Decimal(0)
_LATEST = datetime.max.replace(tzinfo=UTC)


class CostMethod(StrEnum):
    AVERAGE = "average"
    FIFO = "fifo"


@dataclass(frozen=True, slots=True)
class HoldingRow:
    valuation: PositionValuation
    price: PricePoint | None
    fx: FxPoint | None
    value_usd: Decimal | None
    stale: bool


@dataclass(frozen=True, slots=True)
class CashRow:
    account: str
    currency: Currency
    amount: Decimal
    amount_try: Decimal | None
    amount_usd: Decimal | None


@dataclass
class HoldingsReport:
    as_of: date
    known_at: datetime | None
    method: CostMethod
    positions: list[HoldingRow] = field(default_factory=list)
    cash: list[CashRow] = field(default_factory=list)
    usd_try: FxPoint | None = None
    realized: list[Realization] = field(default_factory=list)
    income: list[IncomeEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True when every position and cash balance has a TRY value."""
        return all(r.valuation.value_try is not None for r in self.positions) and all(
            c.amount_try is not None for c in self.cash
        )

    @property
    def total_try(self) -> Decimal:
        """Sum of known TRY values. Check ``complete`` before trusting it as net worth."""
        return sum((r.valuation.value_try or ZERO for r in self.positions), ZERO) + sum(
            (c.amount_try or ZERO for c in self.cash), ZERO
        )

    @property
    def total_usd(self) -> Decimal | None:
        if self.usd_try is None:
            return None
        return self.total_try / self.usd_try.rate_try

    @property
    def unrealized_try(self) -> Decimal:
        return sum((r.valuation.unrealized_try or ZERO for r in self.positions), ZERO)


def build_report(
    transactions: Sequence[Transaction],
    market: MarketData,
    *,
    as_of: date,
    known_at: datetime | None = None,
    accounts: Collection[str] | None = None,
    method: CostMethod = CostMethod.AVERAGE,
    stale_after_days: int = 7,
) -> HoldingsReport:
    if known_at is not None:
        known_at = ensure_utc(known_at)
    facts_as_of = known_at or _LATEST
    state = replay(transactions, as_of=as_of, known_at=known_at)
    report = HoldingsReport(as_of=as_of, known_at=known_at, method=method)
    report.warnings.extend(state.warnings)
    report.usd_try = market.fx_try(USD, as_of, as_of=facts_as_of)
    if report.usd_try is None:
        report.warnings.append(f"no USD/TRY rate on or before {as_of}: USD view unavailable")
    usd_rate = report.usd_try.rate_try if report.usd_try else None
    stale_limit = as_of - timedelta(days=stale_after_days)

    def wanted(account: str) -> bool:
        return accounts is None or account in accounts

    for pos in state.open_positions():
        if not wanted(pos.account):
            continue
        cost, cost_try = (
            (pos.cost, pos.cost_try)
            if method is CostMethod.AVERAGE
            else (pos.fifo_cost, pos.fifo_cost_try)
        )
        price = market.price(pos.instrument, as_of, as_of=facts_as_of)
        fx = try_rate(market, pos.currency, as_of, as_of=facts_as_of)
        if price is not None and price.currency != pos.currency:
            report.warnings.append(
                f"{pos.instrument}: price in {price.currency}, position in {pos.currency}; ignored"
            )
            price = None
        if price is None:
            report.warnings.append(f"{pos.account} {pos.instrument}: no price on or before {as_of}")
        if fx is None:
            report.warnings.append(
                f"{pos.account} {pos.instrument}: no {pos.currency}/TRY rate on or before {as_of}"
            )
        stale = price is not None and price.valid_date < stale_limit
        if stale and price is not None:
            report.warnings.append(
                f"{pos.instrument}: price from {price.valid_date} "
                f"is older than {stale_after_days} days"
            )
        v = value_position(
            pos.account,
            pos.instrument,
            pos.currency,
            pos.quantity,
            cost,
            cost_try,
            price.price if price else None,
            fx.rate_try if fx else None,
        )
        value_usd = v.value_try / usd_rate if v.value_try is not None and usd_rate else None
        report.positions.append(HoldingRow(v, price, fx, value_usd, stale))

    for (account, currency), amount in state.cash_balances().items():
        if not wanted(account):
            continue
        fx = try_rate(market, currency, as_of, as_of=facts_as_of)
        amount_try = amount * fx.rate_try if fx else None
        if fx is None:
            report.warnings.append(
                f"{account} cash {currency}: no {currency}/TRY rate on or before {as_of}"
            )
        amount_usd = amount_try / usd_rate if amount_try is not None and usd_rate else None
        report.cash.append(CashRow(account, currency, amount, amount_try, amount_usd))

    year_start = date(as_of.year, 1, 1)
    report.realized = [
        r for r in state.realizations if wanted(r.account) and r.trade_date >= year_start
    ]
    report.income = [i for i in state.income if wanted(i.account) and i.trade_date >= year_start]
    return report
