"""Wiring for apps: a risk report for a scope on a day (ADR 0010, 14-16)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from qp.core import Currency, InstrumentId, ensure_utc
from qp.data.market import PreloadedMarketData
from qp.performance.history import LATEST, build_history, valuation_days
from qp.performance.metrics import Summary, View, performance
from qp.performance.service import Context
from qp.platform.db import Database
from qp.portfolio.holdings import HoldingsReport, build_report
from qp.reference.repo import ReferenceRepository
from qp.risk.correlation import CorrelationMatrix, correlations
from qp.risk.exposure import AccountHolding, ExposureReport, exposures
from qp.risk.var import Holding, VarResult, holdings_var, portfolio_var


@dataclass
class RiskReport:
    day: date
    holdings: HoldingsReport
    exposure: ExposureReport
    portfolio: VarResult | None
    simulation: VarResult
    correlation: CorrelationMatrix
    volatility: float | None = None
    notes: list[str] = field(default_factory=list)


def asset_classes(ctx: Context) -> dict[InstrumentId, str]:
    """Instrument -> asset class from the instrument master (empty if unreachable)."""
    db = Database.from_settings(ctx.settings.db)
    try:
        with db.session() as session:
            return {i.id: i.asset_class.value for i in ReferenceRepository(session).instruments()}
    finally:
        db.dispose()


def holdings_of(report: HoldingsReport) -> list[AccountHolding]:
    """Valued holdings of a report; items without a TRY value are left out."""
    out: list[AccountHolding] = []
    for row in report.positions:
        v = row.valuation
        if v.value_try is not None:
            out.append(AccountHolding(v.account, Holding(v.instrument, v.currency, v.value_try)))
    for c in report.cash:
        if c.amount_try is not None:
            out.append(AccountHolding(c.account, Holding(None, c.currency, c.amount_try)))
    return out


def aggregate(items: list[AccountHolding]) -> list[Holding]:
    """One holding per instrument / cash currency, accounts summed."""
    totals: dict[tuple[InstrumentId | None, Currency], Decimal] = defaultdict(Decimal)
    for i in items:
        totals[(i.holding.instrument, i.holding.currency)] += i.holding.value_try
    return [
        Holding(inst, cur, v)
        for (inst, cur), v in sorted(totals.items(), key=lambda kv: (str(kv[0][0]), kv[0][1].code))
        if v
    ]


def risk_report(
    ctx: Context,
    *,
    day: date,
    accounts: Collection[str] | None = None,
    known_at: datetime | None = None,
    classes: dict[InstrumentId, str] | None = None,
) -> RiskReport:
    known_at = ensure_utc(known_at) if known_at is not None else None
    as_of = known_at or LATEST
    cfg = ctx.settings.risk
    market = PreloadedMarketData(ctx.market, as_of=as_of, until=day)
    holdings = build_report(
        ctx.transactions,
        market,
        as_of=day,
        known_at=known_at,
        accounts=accounts,
        stale_after_days=ctx.settings.performance.stale_after_days,
    )
    items = holdings_of(holdings)
    notes: list[str] = []
    if not holdings.complete:
        notes.append("some holdings have no price or rate and are left out of every figure")
    known_classes = classes or {}
    exposure = exposures(
        items,
        lambda h: "cash" if h.instrument is None else known_classes.get(h.instrument, "unknown"),
    )

    history = build_history(
        ctx.transactions,
        market,
        end=day,
        accounts=accounts,
        known_at=known_at,
        stale_after_days=ctx.settings.performance.stale_after_days,
    )
    summary = performance(history, View.TRY)
    port: VarResult | None = None
    vol: float | None = None
    if isinstance(summary, Summary):
        port = portfolio_var(
            summary.returns,
            summary.end_value,
            lookback=cfg.lookback_days,
            min_observations=cfg.min_observations,
        )
        vol = summary.volatility
    else:
        notes.append(f"portfolio returns unavailable: {summary.reason}")
    first = history.first_day or day
    days = valuation_days(first, day)
    agg = aggregate(items)
    simulation = holdings_var(
        agg,
        days,
        market,
        as_of=as_of,
        lookback=cfg.lookback_days,
        min_observations=cfg.min_observations,
    )
    corr = correlations(
        [h for h in agg if h.instrument is not None or h.currency.code != "TRY"],
        days[-(cfg.lookback_days + 1) :],
        market,
        as_of=as_of,
    )
    return RiskReport(day, holdings, exposure, port, simulation, corr, vol, notes)
