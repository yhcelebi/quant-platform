"""The ledger replay engine: the one implementation of cost basis and realized P&L.

``replay`` takes ledger facts and returns the derived state as of a date, optionally as the
ledger was known at an earlier moment (``known_at``, compared with ``recorded_at``).

Rules (docs/DOMAIN.md, ADR 0005):

- Weighted average cost per (account, instrument); buy fees add to cost, sell fees reduce
  proceeds; cost of a sale = cost x sold / held (exact when selling everything).
- FIFO lots are tracked alongside; every lot also carries its TRY cost at trade-time FX.
- A reversal cancels its original: both are dropped, as if the original never happened.
- Ordering within a date: corporate actions, transfers out, other transactions, transfers
  in; then by trade_time (timed before untimed), recorded_at, sequence, id.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from qp.core import TRY, Currency, InstrumentId, Money
from qp.ledger.domain import (
    EXTERNAL_FLOW_TYPES,
    LedgerError,
    Leg,
    LegKind,
    Transaction,
    TxType,
)
from qp.reference.domain import SHARE_COUNT_ACTIONS

ZERO = Decimal(0)
_MAX_TIME = datetime.max.replace(tzinfo=UTC)

_PHASE: dict[TxType, int] = {
    TxType.SPLIT: 0,
    TxType.BONUS_ISSUE: 0,
    TxType.STOCK_DIVIDEND: 0,
    TxType.MERGER: 0,
    TxType.DELISTING: 0,
    TxType.CASH_DIVIDEND: 0,
    TxType.TRANSFER_OUT: 1,
    TxType.TRANSFER_IN: 3,
}


def order_key(tx: Transaction) -> tuple[date, int, bool, datetime, datetime, int, int]:
    return (
        tx.trade_date,
        _PHASE.get(tx.tx_type, 2),
        tx.trade_time is None,
        tx.trade_time or _MAX_TIME,
        tx.recorded_at,
        tx.sequence,
        tx.id or 0,
    )


# --------------------------------------------------------------------------- state types
@dataclass
class Lot:
    acquired: date
    quantity: Decimal
    cost: Decimal
    cost_try: Decimal


@dataclass
class Position:
    account: str
    instrument: InstrumentId
    currency: Currency
    quantity: Decimal = ZERO
    cost: Decimal = ZERO
    cost_try: Decimal = ZERO
    lots: list[Lot] = field(default_factory=list)

    @property
    def avg_cost(self) -> Decimal | None:
        return self.cost / self.quantity if self.quantity else None

    @property
    def fifo_cost(self) -> Decimal:
        return sum((lot.cost for lot in self.lots), ZERO)

    @property
    def fifo_cost_try(self) -> Decimal:
        return sum((lot.cost_try for lot in self.lots), ZERO)

    @property
    def is_open(self) -> bool:
        return self.quantity != 0


@dataclass(frozen=True, slots=True)
class Realization:
    """A disposal: sale, delisting. Amounts in the instrument currency and in TRY."""

    account: str
    instrument: InstrumentId
    trade_date: date
    tx_type: TxType
    quantity: Decimal
    currency: Currency
    proceeds: Decimal  # net of fees
    cost: Decimal  # weighted average
    proceeds_try: Decimal
    cost_try: Decimal
    fifo_cost: Decimal
    fifo_cost_try: Decimal
    transaction_id: int | None

    @property
    def realized(self) -> Decimal:
        return self.proceeds - self.cost

    @property
    def realized_try(self) -> Decimal:
        return self.proceeds_try - self.cost_try

    @property
    def realized_fifo(self) -> Decimal:
        return self.proceeds - self.fifo_cost

    @property
    def realized_fifo_try(self) -> Decimal:
        return self.proceeds_try - self.fifo_cost_try


@dataclass(frozen=True, slots=True)
class IncomeEvent:
    account: str
    instrument: InstrumentId | None
    trade_date: date
    kind: TxType
    currency: Currency
    gross: Decimal
    withholding: Decimal
    net: Decimal
    fx_rate_try: Decimal
    eligible_quantity: Decimal | None
    transaction_id: int | None


@dataclass(frozen=True, slots=True)
class ExternalFlow:
    """Money entering (+) or leaving (-) an account from outside the platform."""

    account: str
    trade_date: date
    currency: Currency
    amount: Decimal
    transaction_id: int | None


@dataclass(frozen=True, slots=True)
class InTransit:
    """Shares (``instrument`` set) or cash (``instrument`` None) between accounts."""

    transfer_id: str
    from_account: str
    sent: date
    instrument: InstrumentId | None
    currency: Currency
    quantity: Decimal  # shares, or the cash amount


@dataclass
class _InTransit:
    tx: Transaction
    instrument: InstrumentId | None
    currency: Currency
    quantity: Decimal
    cost: Decimal
    cost_try: Decimal
    lots: list[Lot]


@dataclass
class LedgerState:
    positions: dict[tuple[str, InstrumentId], Position] = field(default_factory=dict)
    cash: dict[tuple[str, Currency], Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    realizations: list[Realization] = field(default_factory=list)
    income: list[IncomeEvent] = field(default_factory=list)
    flows: list[ExternalFlow] = field(default_factory=list)
    fees: list[tuple[Transaction, Money]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _history: dict[tuple[str, InstrumentId], list[tuple[date, Decimal]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _transit: dict[str, _InTransit] = field(default_factory=dict)

    def position(self, account: str, instrument: InstrumentId) -> Position | None:
        return self.positions.get((account, instrument))

    def open_positions(self) -> list[Position]:
        return sorted(
            (p for p in self.positions.values() if p.is_open),
            key=lambda p: (p.account, str(p.instrument)),
        )

    def cash_balances(self) -> dict[tuple[str, Currency], Decimal]:
        return {
            k: v
            for k, v in sorted(self.cash.items(), key=lambda kv: (kv[0][0], kv[0][1].code))
            if v != 0
        }

    def in_transit(self) -> list[InTransit]:
        """Assets sent by ``transfer_out`` and not yet received, by transfer id."""
        return [
            InTransit(tid, p.tx.account, p.tx.trade_date, p.instrument, p.currency, p.quantity)
            for tid, p in sorted(self._transit.items())
        ]

    def quantity_before(self, account: str, instrument: InstrumentId, day: date) -> Decimal:
        """Quantity held at the end of the last day strictly before ``day``."""
        qty = ZERO
        for d, q in self._history.get((account, instrument), []):
            if d >= day:
                break
            qty = q
        return qty


# --------------------------------------------------------------------------- replay
def active_transactions(
    transactions: Iterable[Transaction], *, known_at: datetime | None = None
) -> list[Transaction]:
    """The facts replay applies, in replay order: recorded by ``known_at`` (inclusive, when
    given), reversals and the transactions they cancel removed."""
    txs = list(transactions)
    if known_at is not None:
        txs = [t for t in txs if t.recorded_at <= known_at]
    by_id = {t.id: t for t in txs if t.id is not None}
    cancelled: set[int] = set()
    for t in txs:
        if t.tx_type is TxType.REVERSAL:
            assert t.reverses_id is not None
            target = by_id.get(t.reverses_id)
            if target is None:
                raise LedgerError(f"{t.describe()} reverses unknown transaction {t.reverses_id}")
            if target.tx_type is TxType.REVERSAL:
                raise LedgerError(f"{t.describe()} cannot reverse a reversal")
            if t.reverses_id in cancelled:
                raise LedgerError(f"transaction {t.reverses_id} is reversed twice")
            cancelled.add(t.reverses_id)
    active = [
        t
        for t in txs
        if t.tx_type is not TxType.REVERSAL and (t.id is None or t.id not in cancelled)
    ]
    active.sort(key=order_key)
    return active


def replay_steps(
    transactions: Iterable[Transaction],
    *,
    as_of: date | None = None,
    known_at: datetime | None = None,
) -> Iterator[tuple[date, LedgerState]]:
    """Replay day by day: yield ``(trade_date, state)`` after the last transaction of each
    trade date (up to ``as_of``). The same state object is updated and yielded again, so
    read it before advancing. One replay gives every day's state (daily histories)."""
    active = active_transactions(transactions, known_at=known_at)
    if as_of is not None:
        active = [t for t in active if t.trade_date <= as_of]
    state = LedgerState()
    for i, tx in enumerate(active):
        try:
            _apply(state, tx)
        except LedgerError:
            raise
        except (ArithmeticError, ValueError) as exc:
            raise LedgerError(f"{tx.describe()}: {exc}") from exc
        if i + 1 == len(active) or active[i + 1].trade_date != tx.trade_date:
            yield tx.trade_date, state


def replay(
    transactions: Iterable[Transaction],
    *,
    as_of: date | None = None,
    known_at: datetime | None = None,
) -> LedgerState:
    """Derive ledger state from facts. ``as_of`` filters by trade date (inclusive);
    ``known_at`` filters by ``recorded_at`` (inclusive), giving 'as known at that time'."""
    state = LedgerState()
    for _, state in replay_steps(transactions, as_of=as_of, known_at=known_at):  # noqa: B007
        pass
    for transfer_id, pending in state._transit.items():
        state.warnings.append(
            f"transfer {transfer_id} from {pending.tx.account} has no matching transfer_in yet"
        )
    for (account, currency), balance in state.cash_balances().items():
        if balance < 0:
            state.warnings.append(f"{account} {currency.code} cash is negative: {balance}")
    return state


# --------------------------------------------------------------------------- helpers
def _one_position_leg(tx: Transaction) -> Leg:
    legs = tx.legs_of(LegKind.POSITION)
    if len(legs) != 1:
        raise LedgerError(f"{tx.describe()}: expected exactly one position leg, got {len(legs)}")
    return legs[0]


def _get_position(state: LedgerState, tx: Transaction, leg: Leg) -> Position:
    assert leg.instrument is not None
    key = (tx.account, leg.instrument)
    pos = state.positions.get(key)
    if pos is None:
        pos = Position(tx.account, leg.instrument, leg.currency)
        state.positions[key] = pos
    elif pos.currency != leg.currency:
        raise LedgerError(
            f"{tx.describe()}: {leg.instrument} is held in {pos.currency}, leg is in {leg.currency}"
        )
    return pos


def _to_main(tx: Transaction, amount: Decimal, currency: Currency) -> Decimal:
    """Convert a TRY amount into the transaction's main currency using fx_rate_try."""
    main = tx.main_currency
    if main is None or currency == main:
        return amount
    if currency == TRY:
        return amount / tx.fx
    raise LedgerError(
        f"{tx.describe()}: {currency} amount cannot be converted to {main}; "
        "only the main currency and TRY are supported in one transaction"
    )


def _fees_in_main(tx: Transaction) -> Decimal:
    return sum((_to_main(tx, f.amount, f.currency) for f in tx.legs_of(LegKind.FEE)), ZERO)


def _fees_in_try(tx: Transaction) -> Decimal:
    """Fees in TRY: TRY fees as paid, main-currency fees at the transaction's rate."""
    total = ZERO
    for f in tx.legs_of(LegKind.FEE):
        _to_main(tx, f.amount, f.currency)  # same currency rules as the main-currency total
        total += f.amount if f.currency == TRY else f.amount * tx.fx
    return total


def _record_fees(state: LedgerState, tx: Transaction) -> None:
    for f in tx.legs_of(LegKind.FEE):
        if f.amount:
            state.fees.append((tx, Money(f.amount, f.currency)))


def _expect_cash(tx: Transaction, expected: dict[Currency, Decimal]) -> None:
    actual: dict[Currency, Decimal] = defaultdict(Decimal)
    for leg in tx.legs_of(LegKind.CASH):
        actual[leg.currency] += leg.amount
    want = {c: a for c, a in expected.items() if a != 0}
    got = {c: a for c, a in actual.items() if a != 0}
    if want != got:
        raise LedgerError(
            f"{tx.describe()}: cash legs {_fmt_cash(got)} do not match expected {_fmt_cash(want)}"
        )


def _fmt_cash(amounts: dict[Currency, Decimal]) -> dict[str, str]:
    return {c.code: str(a) for c, a in amounts.items()}


def _apply_cash(state: LedgerState, tx: Transaction) -> None:
    for leg in tx.legs_of(LegKind.CASH):
        state.cash[(tx.account, leg.currency)] += leg.amount


def _note_history(state: LedgerState, pos: Position, day: date) -> None:
    state._history[(pos.account, pos.instrument)].append((day, pos.quantity))


def _take_lots(pos: Position, quantity: Decimal) -> list[Lot]:
    """Remove ``quantity`` from the oldest lots; return the removed pieces."""
    taken: list[Lot] = []
    remaining = quantity
    while remaining > 0:
        if not pos.lots:
            raise LedgerError(f"FIFO lots of {pos.instrument} in {pos.account} are exhausted")
        lot = pos.lots[0]
        if lot.quantity <= remaining:
            taken.append(pos.lots.pop(0))
            remaining -= lot.quantity
        else:
            share_cost = lot.cost * remaining / lot.quantity
            share_cost_try = lot.cost_try * remaining / lot.quantity
            taken.append(Lot(lot.acquired, remaining, share_cost, share_cost_try))
            lot.quantity -= remaining
            lot.cost -= share_cost
            lot.cost_try -= share_cost_try
            remaining = ZERO
    return taken


def _remove(pos: Position, quantity: Decimal) -> tuple[Decimal, Decimal, list[Lot]]:
    """Take ``quantity`` out at average cost; returns (cost, cost_try, fifo lots)."""
    if quantity > pos.quantity:
        raise LedgerError(
            f"cannot remove {quantity} {pos.instrument} from {pos.account}: "
            f"only {pos.quantity} held"
        )
    if quantity == pos.quantity:
        cost, cost_try = pos.cost, pos.cost_try
    else:
        cost = pos.cost * quantity / pos.quantity
        cost_try = pos.cost_try * quantity / pos.quantity
    lots = _take_lots(pos, quantity)
    pos.quantity -= quantity
    pos.cost -= cost
    pos.cost_try -= cost_try
    if pos.quantity == 0:
        pos.cost = pos.cost_try = ZERO
        pos.lots.clear()
    return cost, cost_try, lots


def _scale_lots(lots: list[Lot], new_total: Decimal) -> list[Lot]:
    """Scale lot quantities so they sum to ``new_total``; costs unchanged."""
    old_total = sum((lot.quantity for lot in lots), ZERO)
    if not lots or old_total == 0:
        return lots
    scaled = [
        Lot(lot.acquired, lot.quantity * new_total / old_total, lot.cost, lot.cost_try)
        for lot in lots
    ]
    scaled[-1].quantity = new_total - sum((lot.quantity for lot in scaled[:-1]), ZERO)
    return scaled


# --------------------------------------------------------------------------- per type
def _apply(state: LedgerState, tx: Transaction) -> None:
    handler = _HANDLERS.get(tx.tx_type)
    if handler is None:
        raise LedgerError(f"{tx.describe()}: unsupported transaction type")
    handler(state, tx)


def _acquire(state: LedgerState, tx: Transaction, *, with_cash: bool) -> None:
    leg = _one_position_leg(tx)
    assert leg.quantity is not None
    if leg.quantity <= 0:
        raise LedgerError(f"{tx.describe()}: acquisition quantity must be positive")
    pos = _get_position(state, tx, leg)
    cost = leg.amount + _fees_in_main(tx)
    cost_try = leg.amount * tx.fx + _fees_in_try(tx)
    if with_cash:
        expected: dict[Currency, Decimal] = defaultdict(Decimal)
        expected[leg.currency] -= leg.amount
        for f in tx.legs_of(LegKind.FEE):
            expected[f.currency] -= f.amount
        _expect_cash(tx, expected)
    elif tx.legs_of(LegKind.CASH) or tx.legs_of(LegKind.FEE):
        raise LedgerError(f"{tx.describe()}: opening balance cannot move cash or pay fees")
    pos.quantity += leg.quantity
    pos.cost += cost
    pos.cost_try += cost_try
    pos.lots.append(Lot(tx.trade_date, leg.quantity, cost, cost_try))
    _apply_cash(state, tx)
    _record_fees(state, tx)
    _note_history(state, pos, tx.trade_date)


def _buy(state: LedgerState, tx: Transaction) -> None:
    _acquire(state, tx, with_cash=True)


def _rights_subscription(state: LedgerState, tx: Transaction) -> None:
    ca = tx.corporate_action
    assert ca is not None
    assert ca.subscription_price is not None
    leg = _one_position_leg(tx)
    assert leg.quantity is not None
    if leg.instrument != ca.instrument:
        raise LedgerError(f"{tx.describe()}: subscribed instrument differs from the rights issue")
    if leg.amount != leg.quantity * ca.subscription_price:
        raise LedgerError(
            f"{tx.describe()}: value {leg.amount} != {leg.quantity} x subscription price "
            f"{ca.subscription_price}"
        )
    assert ca.ratio is not None
    entitled = state.quantity_before(tx.account, ca.instrument, ca.ex_date) * ca.ratio
    if leg.quantity > entitled:
        state.warnings.append(
            f"{tx.describe()}: subscribed {leg.quantity} > entitlement {entitled} (bought rights?)"
        )
    _acquire(state, tx, with_cash=True)


def _opening_balance(state: LedgerState, tx: Transaction) -> None:
    if tx.legs_of(LegKind.POSITION):
        _acquire(state, tx, with_cash=False)
        return
    for leg in tx.legs_of(LegKind.CASH):
        if leg.amount < 0:
            raise LedgerError(f"{tx.describe()}: opening cash must be positive")
    _apply_cash(state, tx)


def _sell(state: LedgerState, tx: Transaction) -> None:
    leg = _one_position_leg(tx)
    assert leg.quantity is not None
    assert leg.instrument is not None
    if leg.quantity >= 0:
        raise LedgerError(f"{tx.describe()}: sell quantity must be negative on the position leg")
    pos = state.positions.get((tx.account, leg.instrument))
    if pos is None or pos.quantity <= 0:
        raise LedgerError(f"{tx.describe()}: no {leg.instrument} held in {tx.account}")
    if pos.currency != leg.currency:
        raise LedgerError(f"{tx.describe()}: currency {leg.currency} != position {pos.currency}")
    quantity = -leg.quantity
    expected: dict[Currency, Decimal] = defaultdict(Decimal)
    expected[leg.currency] += leg.amount
    for f in tx.legs_of(LegKind.FEE):
        expected[f.currency] -= f.amount
    _expect_cash(tx, expected)
    proceeds = leg.amount - _fees_in_main(tx)
    cost, cost_try, lots = _remove(pos, quantity)
    state.realizations.append(
        Realization(
            account=tx.account,
            instrument=leg.instrument,
            trade_date=tx.trade_date,
            tx_type=tx.tx_type,
            quantity=quantity,
            currency=leg.currency,
            proceeds=proceeds,
            cost=cost,
            proceeds_try=leg.amount * tx.fx - _fees_in_try(tx),
            cost_try=cost_try,
            fifo_cost=sum((lot.cost for lot in lots), ZERO),
            fifo_cost_try=sum((lot.cost_try for lot in lots), ZERO),
            transaction_id=tx.id,
        )
    )
    _apply_cash(state, tx)
    _record_fees(state, tx)
    _note_history(state, pos, tx.trade_date)


def _cash_only(state: LedgerState, tx: Transaction) -> None:
    if tx.legs_of(LegKind.POSITION):
        raise LedgerError(f"{tx.describe()}: unexpected position leg")
    cash = tx.legs_of(LegKind.CASH)
    if len(cash) != 1:
        raise LedgerError(f"{tx.describe()}: expected exactly one cash leg")
    leg = cash[0]
    t = tx.tx_type
    if t in (TxType.DEPOSIT, TxType.INTEREST) and leg.amount <= 0:
        raise LedgerError(f"{tx.describe()}: amount must be positive")
    if t in (TxType.WITHDRAWAL, TxType.FEE, TxType.TAX) and leg.amount >= 0:
        raise LedgerError(f"{tx.describe()}: amount must be negative")
    if t is TxType.FEE:
        fee_total = sum((f.amount for f in tx.legs_of(LegKind.FEE)), ZERO)
        if fee_total != -leg.amount:
            raise LedgerError(f"{tx.describe()}: fee leg {fee_total} != cash {leg.amount}")
        _record_fees(state, tx)
    if t is TxType.TAX:
        tax_total = sum((f.amount for f in tx.legs_of(LegKind.TAX)), ZERO)
        if tax_total != -leg.amount:
            raise LedgerError(f"{tx.describe()}: tax leg {tax_total} != cash {leg.amount}")
    if t is TxType.INTEREST:
        income = tx.legs_of(LegKind.INCOME)
        gross = sum((i.amount for i in income), ZERO)
        tax = sum((x.amount for x in tx.legs_of(LegKind.TAX)), ZERO)
        if gross - tax != leg.amount:
            raise LedgerError(f"{tx.describe()}: interest gross - tax != cash")
        state.income.append(
            IncomeEvent(
                tx.account,
                None,
                tx.trade_date,
                t,
                leg.currency,
                gross,
                tax,
                leg.amount,
                tx.fx,
                None,
                tx.id,
            )
        )
    if t in EXTERNAL_FLOW_TYPES:
        state.flows.append(ExternalFlow(tx.account, tx.trade_date, leg.currency, leg.amount, tx.id))
    _apply_cash(state, tx)


def _fx_conversion(state: LedgerState, tx: Transaction) -> None:
    cash = tx.legs_of(LegKind.CASH)
    if len(cash) != 2 or cash[0].currency == cash[1].currency:
        raise LedgerError(f"{tx.describe()}: needs two cash legs in different currencies")
    if not (cash[0].amount < 0 < cash[1].amount or cash[1].amount < 0 < cash[0].amount):
        raise LedgerError(f"{tx.describe()}: one cash leg must be negative and one positive")
    if tx.legs_of(LegKind.POSITION):
        raise LedgerError(f"{tx.describe()}: unexpected position leg")
    _apply_cash(state, tx)


def _transfer_out(state: LedgerState, tx: Transaction) -> None:
    assert tx.transfer_id is not None
    if tx.transfer_id in state._transit:
        raise LedgerError(f"{tx.describe()}: duplicate transfer_id {tx.transfer_id}")
    positions = tx.legs_of(LegKind.POSITION)
    if positions:
        leg = _one_position_leg(tx)
        assert leg.quantity is not None
        assert leg.instrument is not None
        if leg.quantity >= 0:
            raise LedgerError(f"{tx.describe()}: transfer_out quantity must be negative")
        pos = state.positions.get((tx.account, leg.instrument))
        if pos is None:
            raise LedgerError(f"{tx.describe()}: no {leg.instrument} held")
        cost, cost_try, lots = _remove(pos, -leg.quantity)
        state._transit[tx.transfer_id] = _InTransit(
            tx, leg.instrument, leg.currency, -leg.quantity, cost, cost_try, lots
        )
        _note_history(state, pos, tx.trade_date)
    else:
        cash = tx.legs_of(LegKind.CASH)
        if len(cash) != 1 or cash[0].amount >= 0:
            raise LedgerError(f"{tx.describe()}: cash transfer_out needs one negative cash leg")
        state._transit[tx.transfer_id] = _InTransit(
            tx, None, cash[0].currency, -cash[0].amount, ZERO, ZERO, []
        )
    _apply_cash(state, tx)


def _transfer_in(state: LedgerState, tx: Transaction) -> None:
    assert tx.transfer_id is not None
    pending = state._transit.pop(tx.transfer_id, None)
    if pending is None:
        raise LedgerError(
            f"{tx.describe()}: no earlier transfer_out with transfer_id {tx.transfer_id}"
        )
    positions = tx.legs_of(LegKind.POSITION)
    if pending.instrument is not None:
        leg = _one_position_leg(tx)
        assert leg.quantity is not None
        if leg.instrument != pending.instrument or leg.quantity != pending.quantity:
            raise LedgerError(
                f"{tx.describe()}: does not match transfer_out of "
                f"{pending.quantity} {pending.instrument}"
            )
        pos = _get_position(state, tx, leg)
        pos.quantity += pending.quantity
        pos.cost += pending.cost
        pos.cost_try += pending.cost_try
        pos.lots.extend(pending.lots)
        pos.lots.sort(key=lambda lot: lot.acquired)
        _note_history(state, pos, tx.trade_date)
    else:
        cash = tx.legs_of(LegKind.CASH)
        if (
            positions
            or len(cash) != 1
            or cash[0].currency != pending.currency
            or cash[0].amount != pending.quantity
        ):
            raise LedgerError(
                f"{tx.describe()}: does not match cash transfer_out of "
                f"{pending.quantity} {pending.currency}"
            )
    _apply_cash(state, tx)


def _share_count_action(state: LedgerState, tx: Transaction) -> None:
    ca = tx.corporate_action
    assert ca is not None
    assert ca.action_type in SHARE_COUNT_ACTIONS
    leg = _one_position_leg(tx)
    assert leg.quantity is not None
    if leg.instrument != ca.instrument:
        raise LedgerError(f"{tx.describe()}: position leg instrument differs from the action")
    if leg.amount != 0 or tx.legs_of(LegKind.CASH) or tx.legs_of(LegKind.FEE):
        raise LedgerError(
            f"{tx.describe()}: share-count actions move no money (record cash-in-lieu separately)"
        )
    pos = state.positions.get((tx.account, ca.instrument))
    held = pos.quantity if pos is not None else ZERO
    if pos is None or held <= 0:
        raise LedgerError(f"{tx.describe()}: no {ca.instrument} held at the ex-date")
    expected_delta = held * (ca.share_factor - 1)
    if abs(leg.quantity - expected_delta) >= 1:
        raise LedgerError(
            f"{tx.describe()}: received {leg.quantity}, expected {expected_delta} "
            f"({held} held x factor {ca.share_factor})"
        )
    if leg.quantity != expected_delta:
        state.warnings.append(
            f"{tx.describe()}: received {leg.quantity} vs computed {expected_delta} (rounding)"
        )
    new_quantity = held + leg.quantity
    if new_quantity <= 0:
        raise LedgerError(f"{tx.describe()}: resulting quantity must stay positive")
    pos.lots = _scale_lots(pos.lots, new_quantity)
    pos.quantity = new_quantity
    _note_history(state, pos, tx.trade_date)
    _warn_in_transit(state, tx, ca.instrument)


def _warn_in_transit(state: LedgerState, tx: Transaction, instrument: InstrumentId) -> None:
    """Shares between transfer_out and transfer_in are in no account, so a corporate action
    on them is not applied; say so instead of silently missing it."""
    for transfer_id, pending in state._transit.items():
        if pending.instrument == instrument:
            state.warnings.append(
                f"{tx.describe()}: {pending.quantity} {instrument} in transit "
                f"(transfer {transfer_id}) are not covered by this action"
            )


def _cash_dividend(state: LedgerState, tx: Transaction) -> None:
    ca = tx.corporate_action
    assert ca is not None
    assert ca.amount_per_share is not None
    assert ca.currency is not None
    if tx.legs_of(LegKind.POSITION):
        raise LedgerError(f"{tx.describe()}: dividends do not change positions")
    income = tx.legs_of(LegKind.INCOME)
    if len(income) != 1 or income[0].currency != ca.currency:
        raise LedgerError(f"{tx.describe()}: needs one income leg in {ca.currency}")
    gross = income[0].amount
    withholding = sum((t.amount for t in tx.legs_of(LegKind.TAX)), ZERO)
    net = gross - withholding
    _expect_cash(tx, {ca.currency: net})
    eligible = state.quantity_before(tx.account, ca.instrument, ca.ex_date)
    if eligible <= 0:
        raise LedgerError(f"{tx.describe()}: no {ca.instrument} held before ex-date {ca.ex_date}")
    computed = Money(eligible * ca.amount_per_share, ca.currency).quantize().amount
    minor = Decimal(1).scaleb(-ca.currency.minor_units)
    if abs(gross - computed) > minor:
        raise LedgerError(
            f"{tx.describe()}: gross {gross} != {eligible} eligible x "
            f"{ca.amount_per_share} = {computed}"
        )
    if gross != computed:
        state.warnings.append(f"{tx.describe()}: gross {gross} vs computed {computed} (rounding)")
    _warn_in_transit(state, tx, ca.instrument)
    state.income.append(
        IncomeEvent(
            tx.account,
            ca.instrument,
            tx.trade_date,
            tx.tx_type,
            ca.currency,
            gross,
            withholding,
            net,
            tx.fx,
            eligible,
            tx.id,
        )
    )
    _apply_cash(state, tx)


def _merger(state: LedgerState, tx: Transaction) -> None:
    ca = tx.corporate_action
    assert ca is not None
    assert ca.new_instrument is not None
    assert ca.ratio is not None
    if tx.legs_of(LegKind.CASH):
        raise LedgerError(
            f"{tx.describe()}: mergers with a cash component are not supported; "
            "record the cash part as a separate sell"
        )
    legs = tx.legs_of(LegKind.POSITION)
    old = [leg for leg in legs if leg.instrument == ca.instrument]
    new = [leg for leg in legs if leg.instrument == ca.new_instrument]
    if len(old) != 1 or len(new) != 1 or len(legs) != 2:
        raise LedgerError(
            f"{tx.describe()}: merger needs one leg out of {ca.instrument} "
            f"and one into {ca.new_instrument}"
        )
    old_leg, new_leg = old[0], new[0]
    if new_leg.currency != old_leg.currency:
        raise LedgerError(
            f"{tx.describe()}: cross-currency mergers ({old_leg.currency} -> "
            f"{new_leg.currency}) are not supported; the carried cost would be in the wrong "
            "currency"
        )
    assert old_leg.quantity is not None
    assert new_leg.quantity is not None
    pos = state.positions.get((tx.account, ca.instrument))
    if pos is None or pos.quantity <= 0 or -old_leg.quantity != pos.quantity:
        raise LedgerError(f"{tx.describe()}: the whole {ca.instrument} position must be exchanged")
    expected_new = pos.quantity * ca.ratio
    if new_leg.quantity <= 0 or abs(new_leg.quantity - expected_new) >= 1:
        raise LedgerError(f"{tx.describe()}: received {new_leg.quantity}, expected {expected_new}")
    lots = _scale_lots(
        [Lot(x.acquired, x.quantity, x.cost, x.cost_try) for x in pos.lots], new_leg.quantity
    )
    cost, cost_try = pos.cost, pos.cost_try
    pos.quantity = pos.cost = pos.cost_try = ZERO
    pos.lots.clear()
    _note_history(state, pos, tx.trade_date)
    target = _get_position(state, tx, new_leg)
    target.quantity += new_leg.quantity
    target.cost += cost
    target.cost_try += cost_try
    target.lots.extend(lots)
    target.lots.sort(key=lambda lot: lot.acquired)
    _note_history(state, target, tx.trade_date)


def _delisting(state: LedgerState, tx: Transaction) -> None:
    ca = tx.corporate_action
    assert ca is not None
    leg = _one_position_leg(tx)
    assert leg.quantity is not None
    assert leg.instrument is not None
    pos = state.positions.get((tx.account, ca.instrument))
    if leg.instrument != ca.instrument or pos is None or -leg.quantity != pos.quantity:
        raise LedgerError(
            f"{tx.describe()}: delisting must remove the whole {ca.instrument} position"
        )
    cash = tx.legs_of(LegKind.CASH)
    proceeds = sum((c.amount for c in cash if c.currency == pos.currency), ZERO)
    if any(c.currency != pos.currency or c.amount < 0 for c in cash):
        raise LedgerError(
            f"{tx.describe()}: delisting proceeds must be positive, in {pos.currency}"
        )
    quantity = pos.quantity
    cost, cost_try, lots = _remove(pos, quantity)
    state.realizations.append(
        Realization(
            tx.account,
            ca.instrument,
            tx.trade_date,
            tx.tx_type,
            quantity,
            pos.currency,
            proceeds,
            cost,
            proceeds * tx.fx if proceeds else ZERO,
            cost_try,
            sum((lot.cost for lot in lots), ZERO),
            sum((lot.cost_try for lot in lots), ZERO),
            tx.id,
        )
    )
    _apply_cash(state, tx)
    _note_history(state, pos, tx.trade_date)


_HANDLERS = {
    TxType.OPENING_BALANCE: _opening_balance,
    TxType.DEPOSIT: _cash_only,
    TxType.WITHDRAWAL: _cash_only,
    TxType.FEE: _cash_only,
    TxType.INTEREST: _cash_only,
    TxType.TAX: _cash_only,
    TxType.BUY: _buy,
    TxType.SELL: _sell,
    TxType.FX_CONVERSION: _fx_conversion,
    TxType.TRANSFER_OUT: _transfer_out,
    TxType.TRANSFER_IN: _transfer_in,
    TxType.SPLIT: _share_count_action,
    TxType.BONUS_ISSUE: _share_count_action,
    TxType.STOCK_DIVIDEND: _share_count_action,
    TxType.RIGHTS_SUBSCRIPTION: _rights_subscription,
    TxType.CASH_DIVIDEND: _cash_dividend,
    TxType.MERGER: _merger,
    TxType.DELISTING: _delisting,
}
