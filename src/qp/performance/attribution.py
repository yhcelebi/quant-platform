"""P&L attribution over a window (start, end] (ADR 0010, 12-13).

Buckets: one per instrument (all scope accounts), one per cash currency, and ``other`` for
fees, interest and taxes not tied to an instrument (and FX-conversion spreads).
``P&L_b = V_end,b - V_start,b - inflow_b``. Every movement is an inflow to one bucket and an
equal outflow from another, external flows are inflows to the receiving bucket, so the
buckets sum exactly to ``V_end - V_start - external flows``.

Cash legs in the transaction's main currency use its trade-time rate; other legs and all
position values use the day's market (latest price and TCMB rate on or before the day).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from qp.core import TRY, Currency, ensure_utc
from qp.data.market import MarketHistory, PreloadedMarketData
from qp.ledger.domain import LegKind, Transaction, TxType
from qp.ledger.engine import active_transactions, replay
from qp.performance.history import (
    LATEST,
    Bucket,
    Pricer,
    Valuation,
    cash_bucket,
    external_flows,
    in_scope,
    instrument_bucket,
    value_state,
)
from qp.performance.metrics import Unavailable

ZERO = Decimal(0)
OTHER: Bucket = ("other", "fees, interest, taxes")


@dataclass(frozen=True, slots=True)
class BucketPnl:
    bucket: Bucket
    currency: Currency | None
    start_value: Decimal
    end_value: Decimal
    inflow: Decimal
    pnl: Decimal
    pnl_local: Decimal | None = None  # instruments: P&L in the instrument currency
    price_effect: Decimal | None = None
    fx_effect: Decimal | None = None

    @property
    def label(self) -> str:
        kind, name = self.bucket
        return f"cash {name}" if kind == "cash" else name


@dataclass
class Attribution:
    start: date
    end: date
    start_value: Decimal
    end_value: Decimal
    external_flows: Decimal
    rows: list[BucketPnl] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)

    @property
    def total_pnl(self) -> Decimal:
        return self.end_value - self.start_value - self.external_flows

    @property
    def residual(self) -> Decimal:
        """Sum of bucket P&L minus total P&L: zero by construction."""
        return sum((r.pnl for r in self.rows), ZERO) - self.total_pnl


class _Book:
    def __init__(self) -> None:
        self.inflow: dict[Bucket, Decimal] = defaultdict(Decimal)
        self.inflow_local: dict[Bucket, Decimal] = defaultdict(Decimal)
        self.currency: dict[Bucket, Currency] = {}

    def move(self, to: Bucket, frm: Bucket, value: Decimal) -> None:
        self.inflow[to] += value
        self.inflow[frm] -= value


def attribute(
    transactions: Sequence[Transaction],
    market: MarketHistory,
    *,
    start: date,
    end: date,
    accounts: Collection[str] | None = None,
    known_at: datetime | None = None,
    stale_after_days: int = 7,
) -> Attribution | Unavailable:
    """Attribute the P&L of (start, end]: holdings valued at the end of ``start`` and
    ``end``, movements of the days after ``start`` up to ``end``."""
    if end <= start:
        return Unavailable("end must be after start")
    if known_at is not None:
        known_at = ensure_utc(known_at)
    as_of = known_at or LATEST
    scope = frozenset(accounts) if accounts is not None else None
    active = active_transactions(transactions, known_at=known_at)
    pricer = Pricer(PreloadedMarketData(market, as_of=as_of, until=end), as_of, stale_after_days)
    v_start = value_state(replay(active, as_of=start), start, pricer, scope)
    v_end = value_state(replay(active, as_of=end), end, pricer, scope)
    for v in (v_start, v_end):
        if not v.complete:
            return Unavailable(f"{v.day}: " + "; ".join(v.missing[:3]))

    transfers_out = {
        t.transfer_id: t for t in active if t.tx_type is TxType.TRANSFER_OUT and t.transfer_id
    }
    book = _Book()
    probe = Valuation(end)  # collects missing prices for movements
    external = ZERO
    by_day: dict[date, list[Transaction]] = defaultdict(list)
    for t in active:
        if start < t.trade_date <= end:
            by_day[t.trade_date].append(t)
    for day in sorted(by_day):
        day_txs = by_day[day]
        for item in external_flows(day_txs, transfers_out, day, pricer, scope, probe):
            if item.value_try is None:
                continue
            book.inflow[item.bucket] += item.value_try
            if item.value_local is not None:
                book.inflow_local[item.bucket] += item.value_local
            external += item.value_try
        for tx in day_txs:
            if in_scope(tx.account, scope):
                _internal(book, tx, day, pricer, probe)
    if probe.missing:
        return Unavailable("; ".join(dict.fromkeys(probe.missing[:3])))

    result = Attribution(
        start,
        end,
        v_start.total_try or ZERO,
        v_end.total_try or ZERO,
        external,
        stale=list(dict.fromkeys(v_start.stale + v_end.stale + probe.stale)),
    )
    buckets = set(v_start.buckets) | set(v_end.buckets) | set(book.inflow)
    for bucket in sorted(buckets, key=_order):
        s, e = v_start.buckets.get(bucket), v_end.buckets.get(bucket)
        sv = s.value_try or ZERO if s else ZERO
        ev = e.value_try or ZERO if e else ZERO
        inflow = book.inflow.get(bucket, ZERO)
        pnl = ev - sv - inflow
        present = e or s
        currency = present.currency if present is not None else book.currency.get(bucket)
        row = BucketPnl(bucket, currency, sv, ev, inflow, pnl)
        if bucket[0] == "instrument" and currency is not None:
            local = (
                (e.value_local or ZERO if e else ZERO)
                - (s.value_local or ZERO if s else ZERO)
                - book.inflow_local.get(bucket, ZERO)
            )
            x_end = pricer.fx(currency, end, probe)
            if x_end is not None:
                price_effect = local * x_end
                row = BucketPnl(
                    bucket, currency, sv, ev, inflow, pnl, local, price_effect, pnl - price_effect
                )
        if row.start_value or row.end_value or row.inflow or row.pnl:
            result.rows.append(row)
    return result


def _order(bucket: Bucket) -> tuple[int, str]:
    return ({"instrument": 0, "cash": 1, "other": 2}[bucket[0]], bucket[1])


def _cash_value(
    tx: Transaction,
    currency: Currency,
    amount: Decimal,
    day: date,
    pricer: Pricer,
    probe: Valuation,
) -> Decimal | None:
    if currency == TRY:
        return amount
    if currency == tx.main_currency and tx.fx_rate_try is not None:
        return amount * tx.fx_rate_try
    rate = pricer.fx(currency, day, probe)
    return amount * rate if rate is not None else None


def _internal(book: _Book, tx: Transaction, day: date, pricer: Pricer, probe: Valuation) -> None:
    t = tx.tx_type
    if t in (TxType.DEPOSIT, TxType.WITHDRAWAL, TxType.OPENING_BALANCE) or t in (
        TxType.TRANSFER_OUT,
        TxType.TRANSFER_IN,
    ):
        return  # external (handled above) or a move inside the scope (same buckets)
    if t is TxType.MERGER:
        ca = tx.corporate_action
        assert ca is not None
        assert ca.new_instrument is not None
        for leg in tx.legs_of(LegKind.POSITION):
            if leg.instrument == ca.new_instrument:
                assert leg.quantity is not None
                price = pricer.price(ca.new_instrument, leg.currency, day, probe)
                rate = pricer.fx(leg.currency, day, probe)
                if price is None or rate is None:
                    return
                local = leg.quantity * price
                new, old = instrument_bucket(ca.new_instrument), instrument_bucket(ca.instrument)
                book.move(new, old, local * rate)
                book.inflow_local[new] += local
                book.inflow_local[old] -= local
        return
    instrument = None
    positions = tx.legs_of(LegKind.POSITION)
    if positions:
        instrument = positions[0].instrument
    elif tx.corporate_action is not None:
        instrument = tx.corporate_action.instrument
    counterpart = instrument_bucket(instrument) if instrument is not None else OTHER
    for leg in tx.legs_of(LegKind.CASH):
        value = _cash_value(tx, leg.currency, leg.amount, day, pricer, probe)
        if value is None:
            continue
        book.move(cash_bucket(leg.currency), counterpart, value)
        if instrument is not None:
            inst_cur = positions[0].currency if positions else leg.currency
            book.currency[counterpart] = inst_cur
            if leg.currency == inst_cur:
                local = leg.amount
            else:
                rate = pricer.fx(inst_cur, day, probe)
                if rate is None:
                    continue
                local = value / rate
            book.inflow_local[counterpart] -= local
