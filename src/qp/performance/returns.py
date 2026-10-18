"""Return calculations (docs/DOMAIN.md). Pure functions; phase 3 wires them to portfolio
history and benchmarks.

- TWR chains sub-period returns split at external flows: TWR = prod(1 + r_i) - 1, with
  1 + r_i = V_end / V_start (V_start after the flow, V_end before the next flow).
- XIRR solves sum(CF_k / (1 + x)^((t_k - t0) / 365)) = 0, investor-perspective flows,
  actual/365. Float64 analytics, converted at the qp.core boundary.
- Real return = (1 + nominal) / (1 + inflation) - 1. Never nominal - inflation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise

from scipy.optimize import brentq

from qp.core import QPValidationError, to_float64

ONE = Decimal(1)


def sub_period_return(start_value: Decimal, end_value: Decimal) -> Decimal:
    """r = V_end / V_start - 1 for one flow-free sub-period."""
    if start_value <= 0:
        raise QPValidationError(f"sub-period start value must be positive, got {start_value}")
    return end_value / start_value - ONE


def twr(sub_returns: Sequence[Decimal]) -> Decimal:
    """Time-weighted return: prod(1 + r_i) - 1."""
    growth = ONE
    for r in sub_returns:
        if r <= -1:
            raise QPValidationError(f"sub-period return {r} <= -100%")
        growth *= ONE + r
    return growth - ONE


def real_return(nominal: Decimal, inflation: Decimal) -> Decimal:
    """(1 + nominal) / (1 + inflation) - 1, e.g. from TÜFE index levels."""
    if inflation <= -1:
        raise QPValidationError("inflation must be > -100%")
    return (ONE + nominal) / (ONE + inflation) - ONE


def inflation_between(index_start: Decimal, index_end: Decimal) -> Decimal:
    """Inflation over a period from two price-index levels (e.g. TÜFE)."""
    if index_start <= 0 or index_end <= 0:
        raise QPValidationError("index levels must be positive")
    return index_end / index_start - ONE


def _npv(rate: float, flows: Sequence[tuple[float, float]]) -> float:
    return float(sum(cf / (1.0 + rate) ** t for t, cf in flows))


def xirr(cashflows: Sequence[tuple[date, Decimal]], *, xtol: float = 1e-12) -> float:
    """Money-weighted return (annual, actual/365). Contributions negative, ending value
    positive. Raises if there is no sign change or no root in (-99.99%, 1e6%).

    With several sign changes more than one root can exist; the root closest to 0 found on
    a bracketing grid is returned.
    """
    if len(cashflows) < 2:
        raise QPValidationError("xirr needs at least two cash flows")
    t0 = min(d for d, _ in cashflows)
    flows = [((d - t0).days / 365.0, to_float64(cf)) for d, cf in cashflows]
    if not (any(cf < 0 for _, cf in flows) and any(cf > 0 for _, cf in flows)):
        raise QPValidationError("xirr needs both negative and positive cash flows")
    grid = [
        -0.9999,
        -0.99,
        -0.9,
        -0.75,
        -0.5,
        -0.25,
        -0.1,
        0.0,
        0.1,
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
        100.0,
        10000.0,
    ]
    roots: list[float] = []
    for lo, hi in pairwise(grid):
        f_lo, f_hi = _npv(lo, flows), _npv(hi, flows)
        if f_lo == 0:
            roots.append(lo)
        elif math.copysign(1, f_lo) != math.copysign(1, f_hi):
            roots.append(float(brentq(_npv, lo, hi, args=(flows,), xtol=xtol, maxiter=500)))
    if not roots:
        raise QPValidationError("xirr found no root")
    return min(roots, key=abs)
