"""Daily value and external-flow history of a scope of accounts, in TRY (ADR 0010, 1-5).

``build_history`` replays the ledger once (``replay_steps``) and values the scope on every
valuation day (Monday to Friday, plus days with an external flow):

- value: positions (quantity x latest price x latest TCMB rate), cash (x rate) and assets in
  transit from a scope account; prices and rates known at ``known_at`` (default: all).
- flow: deposits, withdrawals, opening balances and transfers crossing the scope boundary,
  at market value on the day. Everything else is internal and part of the return.

A day with a holding or flow that has no price or rate is incomplete: its value is None and
the missing items are listed. Nothing is estimated.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from qp.core import TRY, Currency, InstrumentId, ensure_utc
from qp.data.market import MarketData, MarketHistory, PreloadedMarketData, try_rate
from qp.ledger.domain import EXTERNAL_FLOW_TYPES, LegKind, Transaction, TxType
from qp.ledger.engine import LedgerState, active_transactions, replay_steps

ZERO = Decimal(0)
LATEST = datetime.max.replace(tzinfo=UTC)

type Bucket = tuple[str, str]  # ("instrument", "THYAO.BIST") | ("cash", "USD")


def instrument_bucket(instrument: InstrumentId) -> Bucket:
    return ("instrument", str(instrument))


def cash_bucket(currency: Currency) -> Bucket:
    return ("cash", currency.code)


@dataclass(frozen=True, slots=True)
class BucketValue:
    """One instrument (all scope accounts) or one cash currency on a day."""

    currency: Currency
    quantity: Decimal  # shares, or the cash amount
    value_local: Decimal | None  # in ``currency``
    value_try: Decimal | None
    fx_try: Decimal | None


@dataclass
class Valuation:
    day: date
    buckets: dict[Bucket, BucketValue] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def total_try(self) -> Decimal | None:
        if self.missing:
            return None
        return sum((b.value_try or ZERO for b in self.buckets.values()), ZERO)


class Pricer:
    """Market lookups for one valuation day, collecting missing and stale items."""

    def __init__(self, market: MarketData, as_of: datetime, stale_after_days: int) -> None:
        self.market = market
        self.as_of = as_of
        self.stale_after_days = stale_after_days

    def fx(self, currency: Currency, day: date, into: Valuation) -> Decimal | None:
        point = try_rate(self.market, currency, day, as_of=self.as_of)
        if point is None:
            into.missing.append(f"no {currency}/TRY rate on or before {day}")
            return None
        if currency != TRY and (day - point.valid_date).days > self.stale_after_days:
            into.stale.append(f"{currency}/TRY rate from {point.valid_date}")
        return point.rate_try

    def price(
        self, instrument: InstrumentId, currency: Currency, day: date, into: Valuation
    ) -> Decimal | None:
        point = self.market.price(instrument, day, as_of=self.as_of)
        if point is None or point.currency != currency:
            why = "no price" if point is None else f"price in {point.currency}, held in {currency}"
            into.missing.append(f"{instrument}: {why} on or before {day}")
            return None
        if (day - point.valid_date).days > self.stale_after_days:
            into.stale.append(f"{instrument} price from {point.valid_date}")
        return point.price

    def value(
        self,
        instrument: InstrumentId | None,
        currency: Currency,
        quantity: Decimal,
        day: date,
        into: Valuation,
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        """(local value, TRY value, rate) of shares (``instrument``) or cash (None)."""
        rate = self.fx(currency, day, into)
        if instrument is None:
            local: Decimal | None = quantity
        else:
            price = self.price(instrument, currency, day, into)
            local = quantity * price if price is not None else None
        value_try = local * rate if local is not None and rate is not None else None
        return local, value_try, rate


def in_scope(account: str, accounts: Collection[str] | None) -> bool:
    return accounts is None or account in accounts


def value_state(
    state: LedgerState,
    day: date,
    pricer: Pricer,
    accounts: Collection[str] | None,
) -> Valuation:
    """Value the scope's holdings in ``state`` on ``day``, bucketed by instrument and cash
    currency (accounts aggregated). Assets in transit belong to the sending account."""
    holdings: dict[tuple[InstrumentId | None, Currency], Decimal] = defaultdict(Decimal)
    for pos in state.open_positions():
        if in_scope(pos.account, accounts):
            holdings[(pos.instrument, pos.currency)] += pos.quantity
    for (account, currency), amount in state.cash_balances().items():
        if in_scope(account, accounts):
            holdings[(None, currency)] += amount
    for item in state.in_transit():
        if in_scope(item.from_account, accounts):
            holdings[(item.instrument, item.currency)] += item.quantity
    v = Valuation(day)
    for (instrument, currency), quantity in sorted(
        holdings.items(), key=lambda kv: (kv[0][0] is None, str(kv[0][0]), kv[0][1].code)
    ):
        if quantity == 0:
            continue
        local, value_try, rate = pricer.value(instrument, currency, quantity, day, v)
        bucket = cash_bucket(currency) if instrument is None else instrument_bucket(instrument)
        v.buckets[bucket] = BucketValue(currency, quantity, local, value_try, rate)
    return v


@dataclass(frozen=True, slots=True)
class FlowItem:
    """One external flow, positive into the scope."""

    transaction: Transaction
    bucket: Bucket
    value_try: Decimal | None
    value_local: Decimal | None = None  # in the instrument's or cash's own currency


def external_flows(
    day_txs: Sequence[Transaction],
    transfers_out: dict[str, Transaction],
    day: date,
    pricer: Pricer,
    accounts: Collection[str] | None,
    into: Valuation,
) -> list[FlowItem]:
    """External flows of one day's transactions, valued at the day's market."""
    items: list[FlowItem] = []

    def add(tx: Transaction, sign: int) -> None:
        for leg in tx.legs:
            if leg.kind is LegKind.POSITION:
                assert leg.instrument is not None
                assert leg.quantity is not None
                local, value, _ = pricer.value(
                    leg.instrument, leg.currency, leg.quantity, day, into
                )
                bucket = instrument_bucket(leg.instrument)
            elif leg.kind is LegKind.CASH:
                local, value, _ = pricer.value(None, leg.currency, leg.amount, day, into)
                bucket = cash_bucket(leg.currency)
            else:
                continue
            items.append(
                FlowItem(
                    tx,
                    bucket,
                    None if value is None else sign * abs(value),
                    None if local is None else sign * abs(local),
                )
            )

    for tx in day_txs:
        if tx.tx_type in EXTERNAL_FLOW_TYPES:
            if in_scope(tx.account, accounts):
                add(tx, 1 if tx.tx_type is TxType.DEPOSIT else -1)
        elif tx.tx_type is TxType.OPENING_BALANCE:
            if in_scope(tx.account, accounts):
                add(tx, 1)
        elif tx.tx_type is TxType.TRANSFER_IN:
            assert tx.transfer_id is not None
            sender = transfers_out.get(tx.transfer_id)
            from_in = sender is not None and in_scope(sender.account, accounts)
            to_in = in_scope(tx.account, accounts)
            if from_in != to_in:
                add(tx, 1 if to_in else -1)
    return items


@dataclass(frozen=True, slots=True)
class DayRecord:
    day: date
    value: Decimal | None  # end of day, after the day's flows; None if incomplete
    flow: Decimal | None  # net external flow of the day; None if incomplete
    missing: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()


@dataclass
class History:
    accounts: frozenset[str] | None
    known_at: datetime | None
    days: list[DayRecord] = field(default_factory=list)

    def window(self, start: date | None = None, end: date | None = None) -> list[DayRecord]:
        """Records from the last valuation day on or before ``start`` (the window's starting
        value: a YTD window starting on a weekend 31 Dec starts at the 30th's close) to the
        last one on or before ``end``."""
        days = [d for d in self.days if end is None or d.day <= end]
        if start is None:
            return days
        first = 0
        for i, d in enumerate(days):
            if d.day <= start:
                first = i
            else:
                break
        return days[first:]

    @property
    def first_day(self) -> date | None:
        return self.days[0].day if self.days else None


def valuation_days(start: date, end: date, extra: Collection[date] = ()) -> list[date]:
    """Weekdays in [start, end] plus ``extra`` days inside it."""
    out = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    out = {d for d in out if d.weekday() < 5} | {d for d in extra if start <= d <= end}
    return sorted(out)


def _scope_activity(
    active: Sequence[Transaction],
    transfers_out: dict[str, Transaction],
    accounts: Collection[str] | None,
) -> list[Transaction]:
    def touches(tx: Transaction) -> bool:
        if in_scope(tx.account, accounts):
            return True
        if tx.tx_type is TxType.TRANSFER_IN and tx.transfer_id in transfers_out:
            return in_scope(transfers_out[tx.transfer_id].account, accounts)
        return False

    return [t for t in active if touches(t)]


def build_history(
    transactions: Sequence[Transaction],
    market: MarketHistory,
    *,
    end: date,
    start: date | None = None,
    accounts: Collection[str] | None = None,
    known_at: datetime | None = None,
    stale_after_days: int = 7,
) -> History:
    """Daily history from ``start`` (default: the scope's first transaction) to ``end``."""
    if known_at is not None:
        known_at = ensure_utc(known_at)
    as_of = known_at or LATEST
    scope = frozenset(accounts) if accounts is not None else None
    history = History(scope, known_at)
    active = active_transactions(transactions, known_at=known_at)
    transfers_out = {
        t.transfer_id: t for t in active if t.tx_type is TxType.TRANSFER_OUT and t.transfer_id
    }
    scoped = _scope_activity(active, transfers_out, scope)
    if not scoped:
        return history
    first = start or scoped[0].trade_date
    if first > end:
        return history
    by_day: dict[date, list[Transaction]] = defaultdict(list)
    for t in active:
        if t.trade_date <= end:
            by_day[t.trade_date].append(t)
    pricer = Pricer(PreloadedMarketData(market, as_of=as_of, until=end), as_of, stale_after_days)
    flow_days = {t.trade_date for t in scoped if _maybe_external(t)}
    days = valuation_days(first, end, flow_days)

    # replay_steps updates one state object, so advance it only when a valuation day has
    # reached the next trade date (peeking ahead would apply that date's transactions).
    steps: Iterator[tuple[date, LedgerState]] = replay_steps(active, as_of=end)
    step_dates = sorted({t.trade_date for t in active if t.trade_date <= end})
    next_step = 0
    state = LedgerState()
    previous_day: date | None = None
    for day in days:
        while next_step < len(step_dates) and step_dates[next_step] <= day:
            stepped, state = next(steps)
            assert stepped == step_dates[next_step]
            next_step += 1
        valuation = value_state(state, day, pricer, scope)
        # Flows since the previous valuation day. Flow days are valuation days, so this is
        # normally just ``day``; the loop keeps any other day's flow from being dropped.
        flow_items: list[FlowItem] = []
        cursor = previous_day + timedelta(days=1) if previous_day else day
        while cursor <= day:
            flow_items += external_flows(
                by_day.get(cursor, []), transfers_out, cursor, pricer, scope, valuation
            )
            cursor += timedelta(days=1)
        complete = valuation.complete
        flow = (
            sum((f.value_try or ZERO for f in flow_items), ZERO)
            if complete and all(f.value_try is not None for f in flow_items)
            else None
        )
        history.days.append(
            DayRecord(
                day,
                valuation.total_try if flow is not None else None,
                flow,
                tuple(dict.fromkeys(valuation.missing)),
                tuple(dict.fromkeys(valuation.stale)),
            )
        )
        previous_day = day
    return history


def _maybe_external(tx: Transaction) -> bool:
    return tx.tx_type in EXTERNAL_FLOW_TYPES or tx.tx_type in (
        TxType.OPENING_BALANCE,
        TxType.TRANSFER_IN,
    )
