"""Wiring for apps: load the ledger, market data and series, and build performance reports."""

from __future__ import annotations

import calendar
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from qp.core import USD, ensure_utc
from qp.data.lake import CleanStore
from qp.data.market import MarketHistory, PreloadedMarketData
from qp.data.series import LakeSeries, LayeredSeries, ManualSeries, SeriesData
from qp.ledger.domain import Account, Transaction
from qp.ledger.repo import LedgerRepository
from qp.performance.attribution import Attribution, attribute
from qp.performance.benchmarks import (
    BenchmarkKind,
    BenchmarkResult,
    BenchmarkSpec,
    benchmark,
)
from qp.performance.history import LATEST, History, build_history
from qp.performance.metrics import (
    InflationIndex,
    Summary,
    Unavailable,
    View,
    performance,
    period_returns,
)
from qp.platform.db import Database
from qp.platform.lake import LakePaths
from qp.platform.settings import Settings
from qp.portfolio.service import market_data


@dataclass
class Context:
    """Everything a report reads, loaded once (the terminal reuses it across widgets)."""

    settings: Settings
    transactions: list[Transaction]
    accounts: list[Account]
    market: MarketHistory
    series: SeriesData


def series_data(settings: Settings) -> LayeredSeries:
    """Owner-entered series.csv first, then the lake (EVDS, FRED)."""
    return LayeredSeries(
        [
            ManualSeries.from_file(settings.manual.dir / "series.csv"),
            LakeSeries(CleanStore(LakePaths(settings.lake.root))),
        ]
    )


def load_context(settings: Settings) -> Context:
    db = Database.from_settings(settings.db)
    try:
        with db.session() as session:
            repo = LedgerRepository(session)
            txs = repo.transactions()
            accounts = repo.accounts()
    finally:
        db.dispose()
    return Context(settings, txs, accounts, market_data(settings), series_data(settings))


def benchmark_specs(settings: Settings) -> list[BenchmarkSpec]:
    return [
        BenchmarkSpec(b.name, BenchmarkKind(b.kind), b.currency, b.instrument, b.series, b.scale)
        for b in settings.performance.benchmarks
    ]


def months_before(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def standard_periods(end: date) -> dict[str, date | None]:
    """Label -> window start (the close of that day is the starting value)."""
    return {
        "1M": months_before(end, 1),
        "3M": months_before(end, 3),
        "YTD": date(end.year - 1, 12, 31),
        "1Y": months_before(end, 12),
        "3Y": months_before(end, 36),
        "Since start": None,
    }


@dataclass
class PerformanceReport:
    end: date
    view: View
    known_at: datetime | None
    history: History
    summary: Summary | Unavailable
    benchmarks: list[tuple[BenchmarkSpec, BenchmarkResult | Unavailable]] = field(
        default_factory=list
    )
    periods: dict[str, Summary | Unavailable] = field(default_factory=dict)
    monthly: dict[tuple[int, int], Decimal] = field(default_factory=dict)
    yearly: dict[tuple[int, int], Decimal] = field(default_factory=dict)
    inflation_until: date | None = None


class _Views:
    """USD/TRY and inflation lookups for one report."""

    def __init__(self, ctx: Context, market: PreloadedMarketData, end: date, as_of: datetime):
        self.market = market
        self.as_of = as_of
        code = ctx.settings.performance.inflation_series
        self.index = InflationIndex(ctx.series.history(code, end, as_of=as_of)) if code else None

    def usd_try(self, d: date) -> Decimal | None:
        point = self.market.fx_try(USD, d, as_of=self.as_of)
        return point.rate_try if point else None


def performance_report(
    ctx: Context,
    *,
    end: date,
    start: date | None = None,
    view: View = View.TRY,
    accounts: Collection[str] | None = None,
    known_at: datetime | None = None,
    with_periods: bool = True,
) -> PerformanceReport:
    """Performance over [start, end] (default: since the first transaction), with benchmarks
    over the same days and, optionally, the standard periods ending at ``end``."""
    known_at = ensure_utc(known_at) if known_at is not None else None
    as_of = known_at or LATEST
    market = PreloadedMarketData(ctx.market, as_of=as_of, until=end)
    stale = ctx.settings.performance.stale_after_days
    history = build_history(
        ctx.transactions,
        market,
        end=end,
        accounts=accounts,
        known_at=known_at,
        stale_after_days=stale,
    )
    views = _Views(ctx, market, end, as_of)

    def run(s: date | None) -> Summary | Unavailable:
        return performance(
            history, view, start=s, end=end, usd_try=views.usd_try, index=views.index
        )

    summary = run(start)
    report = PerformanceReport(end, view, known_at, history, summary)
    report.inflation_until = views.index.available_until if views.index else None
    if isinstance(summary, Summary):
        days = [p.day for p in summary.values]
        for spec in benchmark_specs(ctx.settings):
            report.benchmarks.append(
                (
                    spec,
                    benchmark(
                        spec,
                        days,
                        view,
                        market,
                        ctx.series,
                        as_of=as_of,
                        usd_try=views.usd_try,
                        index=views.index,
                    ),
                )
            )
        report.monthly = period_returns(summary.returns, by="month")
        report.yearly = period_returns(summary.returns, by="year")
    if with_periods and history.first_day is not None:
        for label, s in standard_periods(end).items():
            if s is not None and s < history.first_day:
                report.periods[label] = Unavailable(f"history starts {history.first_day}")
            else:
                report.periods[label] = run(s)
    return report


def attribution_report(
    ctx: Context,
    *,
    start: date,
    end: date,
    accounts: Collection[str] | None = None,
    known_at: datetime | None = None,
) -> Attribution | Unavailable:
    return attribute(
        ctx.transactions,
        ctx.market,
        start=start,
        end=end,
        accounts=accounts,
        known_at=known_at,
        stale_after_days=ctx.settings.performance.stale_after_days,
    )


def default_start(end: date) -> date:
    """Start of the default attribution window: the year's start (31 Dec close)."""
    return date(end.year, 1, 1) - timedelta(days=1)
