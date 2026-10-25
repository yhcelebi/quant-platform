"""Wiring for apps: data from settings, cost profiles, runs stored with provenance."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from qp.backtest.data import PriceSeries, fx_series, price_series
from qp.backtest.engine import run_nautilus
from qp.backtest.reference import SimResult
from qp.backtest.results import store
from qp.backtest.walkforward import WalkForwardResult, sim_metrics, walk_forward
from qp.core import Currency, InstrumentId, QPError, istanbul_date
from qp.data.service import market_data
from qp.platform.settings import Settings
from qp.strategies.costs import COSTLESS, CostModel, from_settings


def cost_model(settings: Settings, name: str) -> CostModel:
    if name == "costless":
        return COSTLESS
    profiles = settings.backtest.costs
    if name not in profiles:
        raise QPError(
            f"unknown cost profile {name!r}; configured: {sorted(profiles)} (or costless)"
        )
    return from_settings(name, profiles[name])


def load_series(
    settings: Settings,
    *,
    instrument: str | None,
    fx: str | None,
    as_of: datetime,
    start: date | None,
    end: date | None,
) -> PriceSeries:
    if (instrument is None) == (fx is None):
        raise QPError("give exactly one of instrument or fx")
    market = market_data(settings)
    last = end or istanbul_date(as_of)
    if fx is not None:
        s = fx_series(market, Currency(fx), end=last, as_of=as_of)
    else:
        assert instrument is not None
        s = price_series(market, InstrumentId.parse(instrument), end=last, as_of=as_of)
    return s.window(start, end)


def run(
    settings: Settings,
    *,
    series: PriceSeries,
    signal: str,
    params: dict[str, Any],
    costs: str,
    capital: Decimal | None = None,
    repo: Path = Path("."),
) -> tuple[SimResult, Path]:
    cap = capital or settings.backtest.capital
    sim = run_nautilus(
        series,
        signal=signal,
        params=params,
        capital=cap,
        costs=cost_model(settings, costs),
        lag=settings.backtest.lag_bars,
    )
    spec = {
        "signal": signal,
        "params": params,
        "instrument": str(series.instrument),
        "currency": series.currency.code,
        "source": series.source,
        "start": series.days[0],
        "end": series.days[-1],
        "bars": len(series.days),
        "capital": cap,
        "costs": costs,
        "lag_bars": settings.backtest.lag_bars,
        "engine": "nautilus_trader",
    }
    folder = store(
        settings, kind="backtest", spec=spec, metrics=sim_metrics(sim), sim=sim, repo=repo
    )
    return sim, folder


def run_walk_forward(
    settings: Settings,
    *,
    series: PriceSeries,
    signal: str,
    costs: str,
    train: int,
    test: int,
    capital: Decimal | None = None,
    repo: Path = Path("."),
) -> tuple[WalkForwardResult, Path]:
    cap = capital or settings.backtest.capital
    wf = walk_forward(
        series,
        signal=signal,
        capital=cap,
        costs=cost_model(settings, costs),
        train=train,
        test=test,
        lag=settings.backtest.lag_bars,
    )
    spec = {
        "signal": signal,
        "instrument": str(series.instrument),
        "currency": series.currency.code,
        "source": series.source,
        "start": series.days[0],
        "end": series.days[-1],
        "train": train,
        "test": test,
        "capital": cap,
        "costs": costs,
        "lag_bars": settings.backtest.lag_bars,
        "engine": "nautilus_trader (OOS); reference (in-sample grid)",
    }
    folder = store(settings, kind="walk_forward", spec=spec, metrics=wf.metrics, walk=wf, repo=repo)
    return wf, folder
