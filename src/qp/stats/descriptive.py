"""Volatility, tail risk, drawdowns and correlation on float64 samples (ADR 0010).

Conventions:

- Volatility: sample standard deviation (ddof 1) x sqrt(periods per year).
- Losses are positive numbers. Historical ``VaR_a`` is the ceil(a * n)-th smallest loss
  (empirical quantile, no interpolation); ``ES_a`` is the mean of the losses >= ``VaR_a``.
- Parametric (normal): ``VaR = z_a * sigma - mu``, ``ES = sigma * phi(z_a) / (1 - a) - mu``,
  in the units of the returns (multiply by a value outside).
- Drawdowns are measured on a wealth index (e.g. the TWR index), never on raw values.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from qp.core import QPValidationError


def _array(values: Sequence[float], name: str, minimum: int) -> np.ndarray:
    a = np.asarray(values, dtype=np.float64)
    if a.ndim != 1 or a.size < minimum:
        raise QPValidationError(f"{name} needs at least {minimum} values, got {a.size}")
    if not np.all(np.isfinite(a)):
        raise QPValidationError(f"{name} contains non-finite values")
    return a


def _level(level: float) -> None:
    if not 0.5 <= level < 1:
        raise QPValidationError(f"confidence level must be in [0.5, 1), got {level}")


def volatility(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    """Annualized volatility: sample std (ddof 1) x sqrt(periods_per_year)."""
    a = _array(returns, "volatility", 2)
    return float(np.std(a, ddof=1) * math.sqrt(periods_per_year))


def historical_var(losses: Sequence[float], level: float) -> float:
    """The ceil(level * n)-th smallest loss."""
    _level(level)
    a = np.sort(_array(losses, "historical VaR", 1))
    k = math.ceil(level * a.size - 1e-12)  # guard 0.95 * 100 = 95.00000000000001
    return float(a[max(k, 1) - 1])


def historical_es(losses: Sequence[float], level: float) -> float:
    """Mean of the losses at or beyond the historical VaR."""
    var = historical_var(losses, level)
    a = _array(losses, "historical ES", 1)
    return float(np.mean(a[a >= var]))


def parametric_var(returns: Sequence[float], level: float) -> float:
    """Normal VaR of one period, as a fraction of value: z * sigma - mu."""
    _level(level)
    a = _array(returns, "parametric VaR", 2)
    return float(norm.ppf(level) * np.std(a, ddof=1) - np.mean(a))


def parametric_es(returns: Sequence[float], level: float) -> float:
    """Normal ES of one period, as a fraction of value: sigma * phi(z) / (1 - level) - mu."""
    _level(level)
    a = _array(returns, "parametric ES", 2)
    z = norm.ppf(level)
    return float(np.std(a, ddof=1) * norm.pdf(z) / (1 - level) - np.mean(a))


@dataclass(frozen=True, slots=True)
class Drawdown:
    """Indices into the wealth series: peak, trough and recovery (None: not recovered)."""

    depth: float  # <= 0, e.g. -0.25
    peak: int
    trough: int
    recovery: int | None


def drawdown_series(wealth: Sequence[float]) -> list[float]:
    """W_t / max(W_0..W_t) - 1 for a positive wealth index."""
    a = _array(wealth, "drawdown", 1)
    if np.any(a <= 0):
        raise QPValidationError("wealth index must be positive")
    return [float(x) for x in a / np.maximum.accumulate(a) - 1]


def drawdowns(wealth: Sequence[float]) -> list[Drawdown]:
    """Every drawdown episode (peak -> trough -> recovery), deepest first."""
    dd = drawdown_series(wealth)
    episodes: list[Drawdown] = []
    peak = 0
    i = 0
    n = len(dd)
    while i < n:
        if dd[i] == 0:
            peak = i
            i += 1
            continue
        trough = i
        while i < n and dd[i] < 0:
            if dd[i] < dd[trough]:
                trough = i
            i += 1
        episodes.append(Drawdown(dd[trough], peak, trough, i if i < n else None))
    return sorted(episodes, key=lambda e: (e.depth, e.peak))


def correlation(x: Sequence[float], y: Sequence[float]) -> float:
    """Pearson correlation of two aligned samples (nan if either is constant)."""
    a, b = _array(x, "correlation", 3), _array(y, "correlation", 3)
    if a.size != b.size:
        raise QPValidationError("correlation needs samples of equal length")
    if np.std(a) == 0 or np.std(b) == 0:
        return math.nan
    return float(np.corrcoef(a, b)[0, 1])
