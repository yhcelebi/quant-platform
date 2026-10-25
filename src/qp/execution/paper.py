"""Daily paper broker (ADR 0015, 1-7): one idempotent cycle per new bar per deployed
strategy. Same signal, sizing and cost functions as the backtest; every order through the
qp.risk gate and kill switch; reconciled against the ledger bridge after each bar."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from qp.core import Currency, InstrumentId, ensure_utc
from qp.data.market import MarketHistory
from qp.execution.bridge import BrokerFill, ledger_view, transactions
from qp.platform.db import Database
from qp.risk import gate
from qp.risk.models import (
    FillRow,
    HeartbeatRow,
    OrderRow,
    ReconciliationRow,
    SnapshotRow,
)
from qp.strategies.costs import CostModel
from qp.strategies.promotion import Deployment
from qp.strategies.signals import get
from qp.strategies.sizing import order_delta

ZERO = Decimal(0)
BAND = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Bars:
    days: list[date]
    closes: list[Decimal]
    currency: Currency


def bars_for(d: Deployment, market: MarketHistory, *, as_of: datetime, end: date) -> Bars:
    """Closes known at ``as_of`` (rule 5) for the deployment's market."""
    if d.fx is not None:
        pts = market.fx_history(Currency(d.fx), end, as_of=as_of)
        return Bars([p.valid_date for p in pts], [p.rate_try for p in pts], Currency("TRY"))
    assert d.instrument is not None
    prices = market.price_history(InstrumentId.parse(d.instrument), end, as_of=as_of)
    cur = prices[0].currency if prices else Currency("TRY")
    return Bars([p.valid_date for p in prices], [p.price for p in prices], cur)


@dataclass
class CycleReport:
    strategy: str
    processed: list[date] = field(default_factory=list)
    orders: int = 0
    rejected: list[str] = field(default_factory=list)
    halted: str | None = None
    reconciled: bool = True
    notes: list[str] = field(default_factory=list)


def _last_snapshot(db: Database, sid: int) -> SnapshotRow | None:
    with db.session() as s:
        row = s.scalars(
            select(SnapshotRow)
            .where(SnapshotRow.strategy_id == sid)
            .order_by(SnapshotRow.bar_date.desc())
        ).first()
        if row is not None:
            s.expunge(row)
        return row


def _first_snapshot_day(db: Database, sid: int) -> date | None:
    with db.session() as s:
        return s.scalar(
            select(SnapshotRow.bar_date)
            .where(SnapshotRow.strategy_id == sid)
            .order_by(SnapshotRow.bar_date)
        )


def fills_of(db: Database, sid: int) -> list[BrokerFill]:
    with db.session() as s:
        rows = s.scalars(
            select(FillRow).where(FillRow.strategy_id == sid).order_by(FillRow.id)
        ).all()
        return [BrokerFill(r.bar_date, r.quantity, r.price, r.fee) for r in rows]


def run_cycle(
    db: Database,
    d: Deployment,
    *,
    market: MarketHistory,
    costs: CostModel,
    as_of: datetime,
    today: date,
    mode: str = "paper",
    limits: gate.Limits | None = None,
) -> CycleReport:
    limits = limits or gate.Limits()
    report = CycleReport(d.name)
    bars = bars_for(d, market, as_of=ensure_utc(as_of), end=today)
    if not bars.days:
        report.notes.append("no data yet")
        _heartbeat(db, d.name, "no data")
        return report
    last = _last_snapshot(db, d.id)
    if last is None:  # paper starts now: no backfill, only a first target
        start = len(bars.days) - 1
        qty, cash, pending, prev_equity = ZERO, d.capital, None, None
    else:
        newer = [i for i, day in enumerate(bars.days) if day > last.bar_date]
        if not newer:
            report.notes.append(f"no new bar after {last.bar_date}")
            _heartbeat(db, d.name, "up to date")
            return report
        start = newer[0]
        qty, cash, pending, prev_equity = last.quantity, last.cash, last.pending_target, last.equity
    fn = get(d.signal).fn
    buffer = max(r.rate for r in costs.schedule)
    for i in range(start, len(bars.days)):
        day, price = bars.days[i], bars.closes[i]
        halted = gate.halted(db, d.name)
        flatten = gate.flatten_requested(db, d.name)
        if flatten:
            pending = 0.0
        if pending is not None and (halted is None or flatten):
            equity = cash + qty * price
            delta = order_delta(pending, equity, price, qty, buffer, BAND)
            decision = gate.check(
                gate.OrderRequest(
                    d.name, mode, today, bars.days[-1], pending, delta, price, equity, prev_equity
                ),
                status=gate.status(db, d.name),
                halted=halted,
                limits=limits,
                flatten=flatten,
            )
            report.orders += 1
            with db.session() as s:
                order = OrderRow(
                    strategy_id=d.id,
                    bar_date=day,
                    target=pending,
                    quantity=delta,
                    price=price,
                    accepted=decision.accepted,
                    reasons="; ".join(decision.reasons),
                )
                s.add(order)
                s.flush()
                if decision.accepted and delta != 0:
                    fee = costs.fee(delta * price, day)
                    s.add(
                        FillRow(
                            order_id=order.id,
                            strategy_id=d.id,
                            bar_date=day,
                            quantity=delta,
                            price=price,
                            fee=fee,
                        )
                    )
                    cash -= delta * price + fee
                    qty += delta
            if not decision.accepted:
                report.rejected.append(f"{day}: {'; '.join(decision.reasons)}")
            if decision.halt:
                gate.set_halt(db, d.name, halted_=True, reason=decision.halt)
                halted = decision.halt
            if flatten and qty == 0:
                gate.set_halt(db, f"flatten:{d.name}", halted_=False, reason="flattened")
                gate.set_halt(db, d.name, halted_=True, reason="flattened on request")
                halted = "flattened on request"
        closes = [float(c) for c in bars.closes[: i + 1]]
        pending = None if halted else fn(closes, **d.params)[-1]
        equity = cash + qty * price
        with db.session() as s:
            s.add(
                SnapshotRow(
                    strategy_id=d.id,
                    bar_date=day,
                    close=price,
                    quantity=qty,
                    cash=cash,
                    equity=equity,
                    pending_target=pending,
                )
            )
        prev_equity = equity
        report.processed.append(day)
        if halted:
            report.halted = halted
    report.reconciled = reconcile(db, d, bars.currency, qty, cash, report)
    _heartbeat(db, d.name, f"processed {len(report.processed)} bar(s)")
    return report


def reconcile(
    db: Database,
    d: Deployment,
    currency: Currency,
    qty: Decimal,
    cash: Decimal,
    report: CycleReport,
) -> bool:
    """Broker position and cash vs the bridged ledger replay; a mismatch halts the strategy."""
    first = _first_snapshot_day(db, d.id)
    assert first is not None
    account = f"PAPER-{d.name}".upper()
    txs = transactions(
        account,
        start=first,
        capital=d.capital,
        cash_currency=currency,
        fills=fills_of(db, d.id),
        instrument=InstrumentId.parse(d.instrument) if d.instrument else None,
        fx=Currency(d.fx) if d.fx else None,
    )
    view = ledger_view(
        txs,
        account,
        cash_currency=currency,
        instrument=InstrumentId.parse(d.instrument) if d.instrument else None,
        fx=Currency(d.fx) if d.fx else None,
    )
    ok = view.quantity == qty and view.cash == cash
    detail = f"broker qty {qty} cash {cash}; ledger qty {view.quantity} cash {view.cash}"
    with db.session() as s:
        s.add(
            ReconciliationRow(
                strategy_id=d.id,
                bar_date=report.processed[-1] if report.processed else first,
                ok=ok,
                detail=detail,
            )
        )
    if not ok:
        gate.set_halt(db, d.name, halted_=True, reason=f"reconciliation mismatch: {detail}")
        report.halted = "reconciliation mismatch"
    return ok


def _heartbeat(db: Database, strategy: str, detail: str) -> None:
    with db.session() as s:
        s.add(HeartbeatRow(component=f"paper:{strategy}", detail=detail))
