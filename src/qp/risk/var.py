"""One-day Value at Risk and Expected Shortfall in TRY (ADR 0010, 14-15).

Two sources:

- :func:`portfolio_var`: the scope's own daily TWR returns x today's value.
- :func:`holdings_var`: today's holdings x each asset's daily TRY returns (price x TCMB rate;
  foreign cash: the rate), historical simulation over the valuation days.

Statistics are float64 analytics (qp.stats); the TRY results come back as Decimal rounded to
kuruş at the qp.core boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from qp.core import TRY, Currency, InstrumentId, float_to_decimal, to_float64, to_float64_list
from qp.data.market import FxPoint, MarketHistory, PricePoint, latest_on
from qp.stats import historical_es, historical_var, parametric_es, parametric_var

LEVELS = (0.95, 0.99)


class Method(StrEnum):
    HISTORICAL = "historical"
    PARAMETRIC = "parametric"


@dataclass(frozen=True, slots=True)
class VarFigure:
    method: Method
    level: float
    var: Decimal  # TRY loss, positive
    es: Decimal


@dataclass
class VarResult:
    source: str  # "portfolio returns" | "holdings simulation"
    value: Decimal  # TRY value the figures apply to
    observations: int
    figures: list[VarFigure] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    unavailable: str | None = None
    scenarios: list[tuple[date, Decimal]] = field(default_factory=list)  # TRY P&L per day

    def figure(self, method: Method, level: float) -> VarFigure | None:
        return next((f for f in self.figures if f.method is method and f.level == level), None)


def _figures(returns: list[float], value: float) -> list[VarFigure]:
    """VaR/ES of ``value`` for one-period ``returns`` (fractions)."""
    losses = [-r * value for r in returns]
    out: list[VarFigure] = []
    for level in LEVELS:
        out.append(
            VarFigure(
                Method.HISTORICAL,
                level,
                float_to_decimal(historical_var(losses, level), places=2),
                float_to_decimal(historical_es(losses, level), places=2),
            )
        )
    for level in LEVELS:
        out.append(
            VarFigure(
                Method.PARAMETRIC,
                level,
                float_to_decimal(parametric_var(returns, level) * value, places=2),
                float_to_decimal(parametric_es(returns, level) * value, places=2),
            )
        )
    return out


def portfolio_var(
    returns: Sequence[tuple[date, Decimal]],
    value: Decimal,
    *,
    lookback: int = 250,
    min_observations: int = 60,
) -> VarResult:
    """VaR/ES of ``value`` from the last ``lookback`` daily TWR returns."""
    window = list(returns)[-lookback:]
    result = VarResult("portfolio returns", value, len(window))
    if len(window) < min_observations:
        result.unavailable = f"{len(window)} daily returns; at least {min_observations} are needed"
        return result
    rs = to_float64_list([r for _, r in window])
    result.figures = _figures(rs, to_float64(value))
    result.scenarios = [(d, r * value) for d, r in window]
    return result


@dataclass(frozen=True, slots=True)
class Holding:
    """A position (``instrument`` set) or cash (``instrument`` None) valued in TRY today."""

    instrument: InstrumentId | None
    currency: Currency
    value_try: Decimal

    @property
    def name(self) -> str:
        return str(self.instrument) if self.instrument is not None else f"cash {self.currency}"


def asset_returns(
    holding: Holding,
    days: Sequence[date],
    market: MarketHistory,
    *,
    as_of: datetime,
) -> tuple[list[float], list[bool]]:
    """Daily TRY returns on ``days`` (zero on days without a fresh observation) and, per
    day, whether the return is a fresh one."""
    if holding.instrument is None and holding.currency == TRY:
        return [0.0] * len(days), [True] * len(days)
    end = days[-1]
    prices: list[PricePoint] = (
        market.price_history(holding.instrument, end, as_of=as_of)
        if holding.instrument is not None
        else []
    )
    fx: list[FxPoint] = (
        market.fx_history(holding.currency, end, as_of=as_of) if holding.currency != TRY else []
    )
    price_days = {p.valid_date for p in prices}
    fx_days = {f.valid_date for f in fx}

    def level(d: date) -> Decimal | None:
        rate = Decimal(1)
        if holding.currency != TRY:
            point = latest_on(fx, d)
            if point is None:
                return None
            rate = point.rate_try
        if holding.instrument is None:
            return rate
        p = latest_on(prices, d)
        return p.price * rate if p is not None else None

    def fresh(d: date) -> bool:
        return (holding.instrument is None or d in price_days) and (
            holding.currency == TRY or d in fx_days
        )

    out: list[float] = []
    mask: list[bool] = []
    last: Decimal | None = None
    for d in days:
        current = level(d) if fresh(d) else None
        if current is not None and last is not None and last > 0:
            out.append(to_float64(current / last - 1))
            mask.append(True)
        else:
            out.append(0.0)
            mask.append(False)
        if current is not None:
            last = current
    return out, mask


def holdings_var(
    holdings: Sequence[Holding],
    days: Sequence[date],
    market: MarketHistory,
    *,
    as_of: datetime,
    lookback: int = 250,
    min_observations: int = 60,
) -> VarResult:
    """Historical simulation of today's holdings over the last ``lookback`` days."""
    window = list(days)[-(lookback + 1) :]
    total = sum((h.value_try for h in holdings), Decimal(0))
    result = VarResult("holdings simulation", total, max(len(window) - 1, 0))
    if len(window) - 1 < min_observations:
        result.unavailable = f"{len(window) - 1} scenario days; at least {min_observations} needed"
        return result
    pnl = [0.0] * (len(window) - 1)
    covered = Decimal(0)
    for h in holdings:
        rets, mask = asset_returns(h, window, market, as_of=as_of)
        fresh = sum(mask[1:])
        if fresh < min_observations:
            weight = h.value_try / total if total else Decimal(0)
            result.excluded.append(f"{h.name} ({weight:.1%} of value): {fresh} fresh returns")
            continue
        v = to_float64(h.value_try)
        for i, r in enumerate(rets[1:]):
            pnl[i] += r * v
        covered += h.value_try
    if covered == 0:
        result.unavailable = "no holding has enough daily price history"
        return result
    result.value = covered
    result.scenarios = [
        (d, float_to_decimal(x, places=2)) for d, x in zip(window[1:], pnl, strict=True)
    ]
    cv = to_float64(covered)
    result.figures = _figures([x / cv for x in pnl], cv)
    return result
