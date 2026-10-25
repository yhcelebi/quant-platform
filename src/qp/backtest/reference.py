"""The vectorized reference backtest (ADR 0014, 7): a plain loop with the same signal, lag,
sizing and costs as ``QPStrategy`` + ``QPFeeModel``. Parity tests require Nautilus to
produce the same fills and cash to the cent; walk-forward searches use this for speed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from qp.backtest.data import PriceSeries
from qp.core import QPValidationError
from qp.strategies.costs import CostModel
from qp.strategies.nautilus import order_delta
from qp.strategies.signals import get

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Fill:
    day: date
    quantity: Decimal  # signed: + buy, - sell
    price: Decimal
    fee: Decimal


@dataclass
class SimResult:
    days: list[date]
    equity: list[Decimal]  # after the day's fills, at its close
    position: list[Decimal]
    cash_end: Decimal
    fills: list[Fill] = field(default_factory=list)
    targets: list[float] = field(default_factory=list)
    engine: str = "reference"

    @property
    def returns(self) -> list[float]:
        return [
            float(b / a - 1) for a, b in zip(self.equity, self.equity[1:], strict=False) if a > 0
        ]

    @property
    def total_fees(self) -> Decimal:
        return sum((f.fee for f in self.fills), ZERO)


def cost_buffer(costs: CostModel) -> Decimal:
    """The largest proportional rate in the schedule: sizing never needs more cash than it has."""
    return max(r.rate for r in costs.schedule)


def simulate(
    series: PriceSeries,
    *,
    signal: str,
    params: dict[str, Any],
    capital: Decimal,
    costs: CostModel,
    lag: int = 1,
    band: Decimal = Decimal("0.01"),
) -> SimResult:
    fn = get(signal).fn
    buffer = cost_buffer(costs)
    cash, qty = capital, ZERO
    closes: list[float] = []
    pending: list[float] = []
    out = SimResult([], [], [], capital)
    for day, price in zip(series.days, series.closes, strict=True):
        if len(pending) >= lag:
            target = pending.pop(0)
            if not 0 <= target <= 1:
                raise QPValidationError(f"target {target} on {day}: cash accounts are long-only")
            delta = order_delta(target, cash + qty * price, price, qty, buffer, band)
            if delta != 0:
                fee = costs.fee(delta * price, day)
                cash -= delta * price + fee
                qty += delta
                out.fills.append(Fill(day, delta, price, fee))
        closes.append(float(price))
        t = fn(closes, **params)[-1]
        out.targets.append(t)
        pending.append(t)
        out.days.append(day)
        out.equity.append(cash + qty * price)
        out.position.append(qty)
    out.cash_end = cash
    return out
