"""Trading costs (ADR 0014, 5): commission with a minimum, BSMV on the commission, half the
spread and slippage, all as rates of notional; dated, so a fill uses the rate in force on
its date. Decimal throughout, rounded half-even to the currency's minor unit."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from qp.core import QPValidationError
from qp.platform.settings import CostEntrySettings

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class CostRates:
    valid_from: date
    commission: Decimal  # fraction of notional, e.g. 0.0015
    min_commission: Decimal  # in the trade currency
    bsmv: Decimal  # tax on the commission, e.g. 0.05
    half_spread: Decimal
    slippage: Decimal

    def __post_init__(self) -> None:
        for name in ("commission", "min_commission", "bsmv", "half_spread", "slippage"):
            if getattr(self, name) < 0:
                raise QPValidationError(f"{name} must be >= 0")

    @property
    def rate(self) -> Decimal:
        """Proportional cost rate without the minimum (for sizing buffers)."""
        return self.commission * (1 + self.bsmv) + self.half_spread + self.slippage


@dataclass(frozen=True, slots=True)
class CostModel:
    name: str
    schedule: tuple[CostRates, ...]  # ascending valid_from
    minor_units: int = 2

    def __post_init__(self) -> None:
        if not self.schedule:
            raise QPValidationError(f"cost model {self.name}: empty schedule")
        dates = [r.valid_from for r in self.schedule]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise QPValidationError(f"cost model {self.name}: valid_from must ascend")

    def rates(self, on: date) -> CostRates:
        current = [r for r in self.schedule if r.valid_from <= on]
        if not current:
            raise QPValidationError(f"cost model {self.name}: no rates in force on {on}")
        return current[-1]

    def fee(self, notional: Decimal, on: date) -> Decimal:
        """Cost of one fill of ``notional`` (absolute value) on ``on``."""
        n = abs(notional)
        if n == 0:
            return ZERO
        r = self.rates(on)
        commission = (
            max(n * r.commission, r.min_commission) if r.commission or r.min_commission else ZERO
        )
        total = commission * (1 + r.bsmv) + n * (r.half_spread + r.slippage)
        return total.quantize(Decimal(1).scaleb(-self.minor_units), rounding=ROUND_HALF_EVEN)


COSTLESS = CostModel("costless", (CostRates(date(1900, 1, 1), ZERO, ZERO, ZERO, ZERO, ZERO),))


def from_settings(name: str, entries: Sequence[CostEntrySettings]) -> CostModel:
    """Build from a ``backtest.costs.<name>`` profile."""
    return CostModel(
        name,
        tuple(
            CostRates(
                e.valid_from, e.commission, e.min_commission, e.bsmv, e.half_spread, e.slippage
            )
            for e in entries
        ),
    )
