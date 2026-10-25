"""Today's holdings through a weight model (ADR 0016, 1-5). A model output, never an order."""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

import numpy as np

from qp.core import TRY, Currency, InstrumentId, QPValidationError, ensure_utc, float_to_decimal
from qp.data.market import MarketHistory, PreloadedMarketData
from qp.optimization.methods import (
    Estimator,
    Matrix,
    Method,
    Weights,
    covariance,
    cvar,
    hrp,
    max_sharpe,
    min_cvar,
    min_variance,
    risk_contributions,
    risk_parity,
)
from qp.performance.history import LATEST, valuation_days
from qp.performance.service import Context
from qp.portfolio.holdings import build_report
from qp.risk.service import aggregate, holdings_of
from qp.risk.var import Holding, asset_returns

PERIODS = 252
ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class AssetWeight:
    name: str
    value_try: Decimal  # today
    current: float  # weight today within the optimized assets
    target: float
    risk_share: float  # share of the target portfolio's variance
    trade_try: Decimal  # buy (+) / sell (-) to reach the target


@dataclass
class OptimizationResult:
    method: Method
    day: date
    observations: int = 0
    first_day: date | None = None
    invested_try: Decimal = ZERO
    assets: list[AssetWeight] = field(default_factory=list)
    volatility: float | None = None  # annualized, target weights
    current_volatility: float | None = None
    cvar: float | None = None  # daily historical CVaR of the target weights, a fraction
    current_cvar: float | None = None
    cvar_level: float = 0.95
    converged: bool = True
    excluded: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unavailable: str | None = None


def returns_matrix(
    holdings: Sequence[Holding],
    days: Sequence[date],
    market: MarketHistory,
    *,
    as_of: datetime,
    min_observations: int,
) -> tuple[list[Holding], Matrix, list[date], list[str]]:
    """Daily TRY returns of each holding on the days where every kept holding has a fresh
    return. Holdings with fewer than ``min_observations`` fresh returns are left out."""
    kept: list[Holding] = []
    series: list[tuple[list[float], list[bool]]] = []
    excluded: list[str] = []
    for h in holdings:
        rets, mask = asset_returns(h, days, market, as_of=as_of)
        fresh = sum(mask[1:])
        if fresh < min_observations:
            excluded.append(f"{h.name}: {fresh} fresh daily returns, {min_observations} needed")
            continue
        kept.append(h)
        series.append((rets, mask))
    idx = [k for k in range(1, len(days)) if all(m[k] for _, m in series)] if series else []
    matrix = np.array([[r[k] for r, _ in series] for k in idx], dtype=np.float64)
    return kept, matrix.reshape(len(idx), len(kept)), [days[k] for k in idx], excluded


def solve(
    method: Method,
    returns: Matrix,
    *,
    cap: float = 1.0,
    estimator: Estimator = Estimator.LEDOIT_WOLF,
    risk_free: float = 0.0,
    cvar_level: float = 0.95,
) -> tuple[Weights, Matrix]:
    """Weights for ``method`` and the per-period covariance used to describe them."""
    cov = covariance(returns, estimator)
    if method is Method.MIN_VARIANCE:
        return min_variance(cov, cap=cap), cov
    if method is Method.MAX_SHARPE:
        mu = returns.mean(axis=0)
        return max_sharpe(mu, cov, rf=risk_free / PERIODS, cap=cap), cov
    if method is Method.RISK_PARITY:
        return risk_parity(cov, cap=cap), cov
    if method is Method.HRP:
        return hrp(cov, cap=cap), cov
    return min_cvar(returns, level=cvar_level, cap=cap), cov


def optimize_holdings(
    holdings: Sequence[Holding],
    days: Sequence[date],
    market: MarketHistory,
    *,
    as_of: datetime,
    day: date,
    method: Method,
    cap: float = 1.0,
    lookback: int = 500,
    min_observations: int = 60,
    estimator: Estimator = Estimator.LEDOIT_WOLF,
    risk_free: float = 0.0,
    cvar_level: float = 0.95,
) -> OptimizationResult:
    """Weights over ``holdings`` (TRY cash is left out: zero variance) from the last
    ``lookback`` valuation days, compared with today's values."""
    result = OptimizationResult(method, day, cvar_level=cvar_level)
    risky = [h for h in holdings if not (h.instrument is None and h.currency == TRY)]
    if len(risky) < len(holdings):
        result.notes.append("TRY cash is not optimized (zero variance); weights cover the rest")
    window = list(days)[-(lookback + 1) :]
    kept, r, used, excluded = returns_matrix(
        risky, window, market, as_of=as_of, min_observations=min_observations
    )
    result.excluded = excluded
    if len(kept) < 2:
        result.unavailable = f"{len(kept)} asset(s) with enough history; at least 2 are needed"
        return result
    if r.shape[0] < min_observations:
        result.unavailable = (
            f"{r.shape[0]} days on which every asset has a fresh return; {min_observations} needed"
        )
        return result
    try:
        w, cov = solve(
            method,
            r,
            cap=cap,
            estimator=estimator,
            risk_free=risk_free,
            cvar_level=cvar_level,
        )
    except QPValidationError as exc:
        result.unavailable = str(exc)
        return result
    values = [h.value_try for h in kept]
    total = sum(values, ZERO)
    current = np.array([float(v / total) for v in values]) if total > 0 else np.zeros(len(kept))
    shares = risk_contributions(w.weights, cov)
    result.observations = r.shape[0]
    result.first_day = used[0]
    result.invested_try = total
    result.converged = w.converged
    result.notes += w.notes
    result.volatility = math.sqrt(float(w.weights @ cov @ w.weights) * PERIODS)
    result.cvar = cvar(w.weights, r, cvar_level)
    if total > 0:
        result.current_volatility = math.sqrt(float(current @ cov @ current) * PERIODS)
        result.current_cvar = cvar(current, r, cvar_level)
    else:
        result.notes.append("none of these assets is held today: trades are not computed")
    for h, v, cw, tw, s in zip(kept, values, current, w.weights, shares, strict=True):
        target_value = float_to_decimal(float(tw) * float(total), places=2)
        result.assets.append(
            AssetWeight(h.name, v, float(cw), float(tw), float(s), target_value - v)
        )
    if not w.converged:
        result.notes.append("the solver did not report convergence; treat the weights as rough")
    return result


def candidate(spec: str, market: MarketHistory, day: date, *, as_of: datetime) -> Holding:
    """An asset not held today, from ``USD`` (cash in a currency) or ``SYMBOL.VENUE``."""
    if "." not in spec:
        return Holding(None, Currency(spec.upper()), ZERO)
    inst = InstrumentId.parse(spec)
    prices = market.price_history(inst, day, as_of=as_of)
    if not prices:
        raise QPValidationError(f"{spec}: no prices known; its currency is unknown")
    return Holding(inst, prices[-1].currency, ZERO)


def optimize(
    ctx: Context,
    *,
    day: date,
    method: Method,
    accounts: Collection[str] | None = None,
    known_at: datetime | None = None,
    extra: Sequence[str] = (),
    cap: float | None = None,
) -> OptimizationResult:
    """Today's holdings in scope (plus ``extra`` assets) through ``method``."""
    known_at = ensure_utc(known_at) if known_at is not None else None
    as_of = known_at or LATEST
    cfg = ctx.settings.optimization
    market = PreloadedMarketData(ctx.market, as_of=as_of, until=day)
    report = build_report(
        ctx.transactions,
        market,
        as_of=day,
        known_at=known_at,
        accounts=accounts,
        stale_after_days=ctx.settings.performance.stale_after_days,
    )
    held = aggregate(holdings_of(report))
    names = {h.name for h in held}
    for spec in extra:
        c = candidate(spec, ctx.market, day, as_of=as_of)
        if c.name not in names:
            held.append(c)
            names.add(c.name)
    # asset history is not limited to the holding period: the last ``lookback`` weekdays
    first = day - timedelta(days=cfg.lookback_days * 7 // 5 + 7)
    result = optimize_holdings(
        held,
        valuation_days(first, day)[-(cfg.lookback_days + 1) :],
        market,
        as_of=as_of,
        day=day,
        method=method,
        cap=cfg.cap if cap is None else cap,
        lookback=cfg.lookback_days,
        min_observations=cfg.min_observations,
        estimator=Estimator(cfg.estimator),
        risk_free=float(cfg.risk_free),
        cvar_level=cfg.cvar_level,
    )
    if not report.complete:
        result.notes.append("some holdings have no price or rate and are left out")
    return result
