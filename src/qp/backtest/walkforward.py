"""Metrics and walk-forward evaluation (ADR 0014, 8).

Walk-forward: rolling windows (train, then test); on each train window every grid point is
simulated (reference engine, parity-tested) and the best Sharpe is chosen; the chosen
parameters then run in NautilusTrader over train + test so the signal has its history, and
only the test days' returns count. Out-of-sample returns are chained across windows.
Every grid point is a trial: the result carries the deflated Sharpe ratio and PBO.
"""

from __future__ import annotations

import itertools
import math
import operator
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np

from qp.backtest.data import PriceSeries
from qp.backtest.engine import run_nautilus
from qp.backtest.reference import SimResult, simulate
from qp.core import QPValidationError
from qp.stats import deflated_sharpe, drawdown_series, pbo, probabilistic_sharpe, volatility
from qp.strategies.costs import CostModel
from qp.strategies.signals import get


@dataclass(frozen=True, slots=True)
class Metrics:
    days: int
    total_return: float
    cagr: float | None  # None under one year (DOMAIN: no annualizing short periods)
    volatility: float | None
    sharpe: float | None
    max_drawdown: float
    trades: int
    fees: float
    exposure: float  # share of days with a position


def _sharpe(r: Sequence[float], periods: int = 252) -> float | None:
    a = np.asarray(r, dtype=np.float64)
    if a.size < 2 or np.std(a, ddof=1) == 0:
        return None
    return float(np.mean(a) / np.std(a, ddof=1) * math.sqrt(periods))


def metrics_of(
    returns: Sequence[float],
    *,
    first: date,
    last: date,
    trades: int = 0,
    fees: float = 0.0,
    exposure: float = 0.0,
) -> Metrics:
    wealth = list(itertools.accumulate((1 + r for r in returns), operator.mul, initial=1.0))
    total = wealth[-1] - 1
    days = (last - first).days
    cagr = (1 + total) ** (365 / days) - 1 if days >= 365 and total > -1 else None
    return Metrics(
        days,
        total,
        cagr,
        volatility(list(returns)) if len(returns) >= 2 else None,
        _sharpe(returns),
        min(drawdown_series(wealth)) if all(w > 0 for w in wealth) else -1.0,
        trades,
        fees,
        exposure,
    )


def sim_metrics(sim: SimResult) -> Metrics:
    return metrics_of(
        sim.returns,
        first=sim.days[0],
        last=sim.days[-1],
        trades=len(sim.fills),
        fees=float(sim.total_fees),
        exposure=sum(1 for q in sim.position if q != 0) / len(sim.position),
    )


def grid_points(signal: str, grid: dict[str, list[Any]] | None = None) -> list[dict[str, Any]]:
    g = grid if grid is not None else get(signal).grid
    if not g:
        return [dict(get(signal).defaults)]
    keys = sorted(g)
    points = [
        dict(zip(keys, values, strict=True)) for values in itertools.product(*(g[k] for k in keys))
    ]
    if signal == "sma_cross":
        points = [p for p in points if p["fast"] < p["slow"]]
    return points


@dataclass(frozen=True, slots=True)
class Window:
    train: tuple[date, date]
    test: tuple[date, date]
    params: dict[str, Any]
    train_sharpe: float | None
    test_return: float


@dataclass
class WalkForwardResult:
    signal: str
    grid: list[dict[str, Any]]
    windows: list[Window] = field(default_factory=list)
    oos_days: list[date] = field(default_factory=list)
    oos_returns: list[float] = field(default_factory=list)
    metrics: Metrics | None = None
    psr: float | None = None
    dsr: float | None = None
    pbo: float | None = None
    notes: list[str] = field(default_factory=list)


def walk_forward(
    series: PriceSeries,
    *,
    signal: str,
    capital: Decimal,
    costs: CostModel,
    train: int = 252,
    test: int = 63,
    lag: int = 1,
    grid: dict[str, list[Any]] | None = None,
) -> WalkForwardResult:
    n = len(series.days)
    if n < train + test:
        raise QPValidationError(f"walk-forward needs at least {train + test} bars, have {n}")
    points = grid_points(signal, grid)
    out = WalkForwardResult(signal, points)
    trial_srs: list[float] = []
    trades, fees, in_market = 0, 0.0, 0
    start = 0
    while start + train + test <= n:
        tr = series.window(series.days[start], series.days[start + train - 1])
        scored = []
        for p in points:
            sr = _sharpe(
                simulate(tr, signal=signal, params=p, capital=capital, costs=costs, lag=lag).returns
            )
            scored.append((sr if sr is not None else -math.inf, p))
            if sr is not None:
                trial_srs.append(sr / math.sqrt(252))  # per-period, for deflation
        best_sr, best = max(scored, key=lambda x: x[0])
        span = series.window(series.days[start], series.days[start + train + test - 1])
        run = run_nautilus(span, signal=signal, params=best, capital=capital, costs=costs, lag=lag)
        test_rets = run.returns[train - 1 :]  # returns of the test days only
        test_days = set(run.days[train:])
        test_fills = [f for f in run.fills if f.day in test_days]
        trades += len(test_fills)
        fees += float(sum((f.fee for f in test_fills), Decimal(0)))
        in_market += sum(1 for q in run.position[train:] if q != 0)
        out.oos_returns += test_rets
        out.oos_days += run.days[train:]
        out.windows.append(
            Window(
                (tr.days[0], tr.days[-1]),
                (run.days[train], run.days[-1]),
                best,
                None if best_sr == -math.inf else best_sr,
                float(np.prod([1 + r for r in test_rets]) - 1),
            )
        )
        start += test
    out.metrics = metrics_of(
        out.oos_returns,
        first=out.oos_days[0],
        last=out.oos_days[-1],
        trades=trades,
        fees=fees,
        exposure=in_market / len(out.oos_days),
    )
    if len(out.oos_returns) >= 3 and np.std(out.oos_returns) > 0:
        out.psr = probabilistic_sharpe(out.oos_returns)
        if len(points) >= 2 and len(trial_srs) >= 2:
            out.dsr = deflated_sharpe(
                out.oos_returns,
                trials=len(points),
                trial_sr_variance=float(np.var(trial_srs, ddof=1)),
            )
    if len(points) >= 2:
        full = [
            simulate(series, signal=signal, params=p, capital=capital, costs=costs, lag=lag).returns
            for p in points
        ]
        m = np.column_stack(full)
        blocks = 16 if m.shape[0] >= 32 * 16 else 8
        if m.shape[0] >= 2 * blocks:
            out.pbo = pbo(m, blocks=blocks).probability
        else:
            out.notes.append("too few bars for PBO")
    else:
        out.notes.append("one parameter set: no selection, DSR and PBO not applicable")
    return out
