"""DCF, WACC, reverse DCF and multiples (ADR 0013, 11). float64 analytics: inputs are
estimates, outputs are ranges of judgement, not ledger money."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from scipy.optimize import brentq

from qp.core import QPValidationError, to_float64
from qp.fundamentals.facts import Snapshot


def wacc(
    *,
    equity_value: float,
    debt_value: float,
    rf: float,
    beta: float,
    erp: float,
    cost_of_debt: float,
    tax_rate: float,
) -> float:
    """E/V (rf + beta ERP) + D/V kd (1 - t)."""
    v = equity_value + debt_value
    if v <= 0 or equity_value < 0 or debt_value < 0:
        raise QPValidationError("equity and debt values must be >= 0 with a positive sum")
    ke = rf + beta * erp
    return equity_value / v * ke + debt_value / v * cost_of_debt * (1 - tax_rate)


@dataclass(frozen=True, slots=True)
class DcfResult:
    enterprise_value: float
    pv_explicit: float
    pv_terminal: float
    equity_value: float
    per_share: float | None

    @property
    def terminal_share(self) -> float:
        """How much of the value sits in the terminal value (a fragility indicator)."""
        return self.pv_terminal / self.enterprise_value if self.enterprise_value else float("nan")


def dcf(
    fcff: Sequence[float],
    *,
    discount_rate: float,
    terminal_growth: float,
    net_debt: float = 0.0,
    shares: float | None = None,
) -> DcfResult:
    """Year-end FCFF forecasts; terminal value FCFF_N (1 + g) / (r - g) at year N."""
    if not fcff:
        raise QPValidationError("need at least one forecast year")
    if terminal_growth >= discount_rate:
        raise QPValidationError("terminal growth must be below the discount rate")
    pv = sum(f / (1 + discount_rate) ** (t + 1) for t, f in enumerate(fcff))
    n = len(fcff)
    tv = fcff[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_tv = tv / (1 + discount_rate) ** n
    ev = pv + pv_tv
    equity = ev - net_debt
    return DcfResult(ev, pv, pv_tv, equity, equity / shares if shares else None)


def grow(base: float, growth: float, years: int) -> list[float]:
    return [base * (1 + growth) ** t for t in range(1, years + 1)]


def reverse_dcf(
    market_ev: float,
    *,
    base_fcff: float,
    years: int,
    discount_rate: float,
    terminal_growth: float,
) -> float:
    """The constant FCFF growth over ``years`` that the market EV implies."""
    if base_fcff <= 0:
        raise QPValidationError("reverse DCF needs a positive base FCFF")

    def gap(g: float) -> float:
        flows = grow(base_fcff, g, years)
        return (
            dcf(
                flows, discount_rate=discount_rate, terminal_growth=terminal_growth
            ).enterprise_value
            - market_ev
        )

    lo, hi = -0.9, 3.0
    if gap(lo) > 0 or gap(hi) < 0:
        raise QPValidationError("no implied growth in [-90%, 300%] matches the market EV")
    return float(brentq(gap, lo, hi, xtol=1e-12))


@dataclass(frozen=True, slots=True)
class Multiples:
    market_cap: float
    enterprise_value: float | None
    pe: float | None
    pb: float | None
    ev_ebitda: float | None
    ev_sales: float | None
    fcf_yield: float | None
    missing: list[str]


def _f(x: Decimal | None) -> float | None:
    return to_float64(x) if x is not None else None


def multiples(s: Snapshot, price: float) -> Multiples:
    """Multiples from a point-in-time snapshot and a price in the same currency."""
    shares = _f(s.shares.value) if s.shares else None
    if not shares or shares <= 0:
        raise QPValidationError("multiples need shares outstanding")
    mcap = price * shares
    missing: list[str] = []
    nd = _f(s.net_debt)
    ev = mcap + nd if nd is not None else None
    if ev is None:
        missing.append("net debt (EV)")

    def ratio(
        num: float | None, den: float | None, name: str, positive: bool = True
    ) -> float | None:
        if num is None or den is None:
            missing.append(name)
            return None
        if positive and den <= 0:
            return None  # a negative earnings multiple is not meaningful
        return num / den

    ni = _f(s.net_income.value) if s.net_income else None
    eq = _f(s.equity.value) if s.equity else None
    rev = _f(s.revenue.value) if s.revenue else None
    fcf = _f(s.fcf)
    return Multiples(
        mcap,
        ev,
        ratio(mcap, ni, "net income"),
        ratio(mcap, eq, "equity"),
        ratio(ev, _f(s.ebitda), "EBITDA"),
        ratio(ev, rev, "revenue"),
        ratio(fcf, mcap, "FCF", positive=False),
        missing,
    )
