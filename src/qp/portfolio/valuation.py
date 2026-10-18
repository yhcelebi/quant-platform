"""Valuing one position, including the FX / price split of unrealized P&L (docs/DOMAIN.md).

With cost C and value V1 in the instrument currency, X0 the (weighted) purchase rate
cost_try / C and X1 the current rate (TRY per unit of the instrument currency):

    FX effect    = C x (X1 - X0)  = C x X1 - cost_try
    price effect = (V1 - C) x X1
    sum          = V1 x X1 - cost_try = unrealized P&L in TRY
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from qp.core import TRY, Currency, InstrumentId, QPValidationError


@dataclass(frozen=True, slots=True)
class PositionValuation:
    account: str
    instrument: InstrumentId
    currency: Currency
    quantity: Decimal
    cost: Decimal
    cost_try: Decimal
    price: Decimal | None
    fx_try: Decimal | None
    value: Decimal | None
    value_try: Decimal | None
    unrealized: Decimal | None
    unrealized_try: Decimal | None
    fx_effect_try: Decimal | None
    price_effect_try: Decimal | None

    @property
    def avg_cost(self) -> Decimal | None:
        return self.cost / self.quantity if self.quantity else None


def value_position(
    account: str,
    instrument: InstrumentId,
    currency: Currency,
    quantity: Decimal,
    cost: Decimal,
    cost_try: Decimal,
    price: Decimal | None,
    fx_try: Decimal | None,
) -> PositionValuation:
    """Value a position. Missing price or FX gives None values, never a guess."""
    if currency == TRY:
        fx_try = Decimal(1)
    if fx_try is not None and fx_try <= 0:
        raise QPValidationError("fx_try must be positive")
    if price is not None and price < 0:
        raise QPValidationError("price must be >= 0")
    value = quantity * price if price is not None else None
    unrealized = value - cost if value is not None else None
    value_try = unrealized_try = fx_effect = price_effect = None
    if value is not None and fx_try is not None:
        value_try = value * fx_try
        unrealized_try = value_try - cost_try
        fx_effect = cost * fx_try - cost_try
        price_effect = (value - cost) * fx_try
    return PositionValuation(
        account,
        instrument,
        currency,
        quantity,
        cost,
        cost_try,
        price,
        fx_try,
        value,
        value_try,
        unrealized,
        unrealized_try,
        fx_effect,
        price_effect,
    )
