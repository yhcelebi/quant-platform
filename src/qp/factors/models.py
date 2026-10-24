"""Factor regressions and quantile factor construction (ADR 0013, 12)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from qp.core import QPValidationError
from qp.stats import Regression, ols


def factor_regression(
    returns: Sequence[float],
    factors: Mapping[str, Sequence[float]],
    *,
    rf: Sequence[float] | None = None,
) -> Regression:
    """Excess returns on factor returns: alpha (``const``), betas, HAC t-statistics."""
    y = list(returns)
    if rf is not None:
        if len(rf) != len(y):
            raise QPValidationError("rf must align with returns")
        y = [r - f for r, f in zip(y, rf, strict=True)]
    return ols(y, {k: list(v) for k, v in factors.items()})


def quantile_long_short(
    characteristic: Mapping[date, Mapping[str, float]],
    forward_returns: Mapping[date, Mapping[str, float]],
    *,
    quantiles: int = 5,
    min_assets: int = 10,
) -> list[tuple[date, float]]:
    """Per date: sort assets by the characteristic known at that date, factor return =
    equal-weighted next-period return of the top quantile minus the bottom quantile.
    ``forward_returns[d]`` must be the return over the period after ``d``."""
    if quantiles < 2:
        raise QPValidationError("quantiles must be >= 2")
    out: list[tuple[date, float]] = []
    for d in sorted(characteristic):
        rets = forward_returns.get(d, {})
        ranked = sorted((v, a) for a, v in characteristic[d].items() if a in rets)
        if len(ranked) < max(min_assets, quantiles):
            continue
        size = len(ranked) // quantiles
        bottom = [rets[a] for _, a in ranked[:size]]
        top = [rets[a] for _, a in ranked[-size:]]
        out.append((d, sum(top) / len(top) - sum(bottom) / len(bottom)))
    return out
