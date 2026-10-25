"""Paper-vs-backtest report (ADR 0015, 8): the reference backtest over the bars a strategy
has paper-traded, day by day. Paper and the reference share signal, sizing and costs, so
differences come only from gate decisions (rejections, halts) or data revised after the
paper cycle ran; both are listed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import select

from qp.backtest.data import PriceSeries
from qp.backtest.reference import simulate
from qp.core import Currency, InstrumentId, QPError
from qp.execution.paper import bars_for
from qp.platform.db import Database
from qp.platform.settings import Settings
from qp.portfolio.service import market_data
from qp.risk.models import OrderRow, SnapshotRow
from qp.strategies.costs import from_settings
from qp.strategies.promotion import deployments


@dataclass(frozen=True, slots=True)
class DayCompare:
    day: date
    paper: Decimal
    backtest: Decimal

    @property
    def diff(self) -> Decimal:
        return self.paper - self.backtest


@dataclass
class PaperReport:
    strategy: str
    days: list[DayCompare] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def max_abs_diff(self) -> Decimal:
        return max((abs(d.diff) for d in self.days), default=Decimal(0))

    @property
    def weeks(self) -> float:
        return (self.days[-1].day - self.days[0].day).days / 7 if self.days else 0.0


def paper_vs_backtest(settings: Settings, db: Database, name: str, *, now: datetime) -> PaperReport:
    d = next((x for x in deployments(db) if x.name == name), None)
    if d is None:
        raise QPError(f"no strategy {name}")
    report = PaperReport(name)
    with db.session() as s:
        snaps = s.scalars(
            select(SnapshotRow)
            .where(SnapshotRow.strategy_id == d.id)
            .order_by(SnapshotRow.bar_date)
        ).all()
        rejected = s.scalars(
            select(OrderRow).where(OrderRow.strategy_id == d.id, OrderRow.accepted.is_(False))
        ).all()
        paper = {r.bar_date: r.equity for r in snaps}
        report.rejections = [f"{r.bar_date}: {r.reasons}" for r in rejected]
    if not paper:
        report.notes.append("no paper days yet")
        return report
    first, last = min(paper), max(paper)
    bars = bars_for(d, market_data(settings), as_of=now, end=last)
    instrument = (
        InstrumentId(f"{d.fx}TRY", "TCMB") if d.fx else InstrumentId.parse(d.instrument or "")
    )
    series = PriceSeries(
        instrument,
        Currency(bars.currency.code),
        bars.days,
        bars.closes,
        [datetime.combine(day, time(), tzinfo=UTC) for day in bars.days],  # unused by the loop
        "paper report",
    )
    costs = from_settings(d.costs, settings.backtest.costs[d.costs])
    ref = simulate(
        series,
        signal=d.signal,
        params=d.params,
        capital=d.capital,
        costs=costs,
        lag=settings.backtest.lag_bars,
        trade_from=first,
    )
    for day, eq in zip(ref.days, ref.equity, strict=True):
        if day in paper:
            report.days.append(DayCompare(day, paper[day], eq))
    if report.max_abs_diff:
        report.notes.append(
            "differences come from gate rejections/halts or data revised after the paper run"
        )
    return report
