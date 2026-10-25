"""Order sizing shared by the backtest, the reference loop and the paper broker (ADR 0014,
4): pure Decimal arithmetic, no Nautilus, so every engine sizes identically."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

ZERO = Decimal(0)


def order_delta(
    target: float,
    equity: Decimal,
    price: Decimal,
    held: Decimal,
    cost_rate: Decimal,
    band: Decimal,
) -> Decimal:
    """Units to trade toward the target; trades worth less than ``band`` x equity are
    skipped (no daily churn from price drift), except a move to flat (ADR 0014, 4)."""
    want = target_quantity(target, equity, price, cost_rate)
    delta = want - held
    if delta != 0 and want != 0 and abs(delta) * price < band * equity:
        return Decimal(0)
    return delta


def target_quantity(target: float, equity: Decimal, price: Decimal, cost_rate: Decimal) -> Decimal:
    """Whole units for a target fraction of equity, truncated toward zero (ADR 0014, 4)."""
    if price <= 0:
        return ZERO
    raw = Decimal(str(target)) * equity / (price * (1 + cost_rate))
    return raw.to_integral_value(rounding=ROUND_DOWN)
