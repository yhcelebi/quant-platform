"""Canonical construction of ledger transactions from plain parameters.

The importer and tests build transactions only through these functions, so every
transaction type has exactly one leg layout (ADR 0005).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from qp.core import TRY, Currency, InstrumentId, Money, QPValidationError
from qp.ledger.domain import Leg, LegKind, Transaction, TxType
from qp.reference.domain import CorporateAction

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Common:
    """Header fields shared by every transaction."""

    account: str
    trade_date: date
    recorded_at: datetime
    sequence: int = 0
    trade_time: datetime | None = None
    settle_date: date | None = None
    external_ref: str | None = None
    note: str = ""
    sleeve: str = "main"


def _tx(c: Common, tx_type: TxType, legs: list[Leg], **kw: object) -> Transaction:
    return Transaction(
        account=c.account,
        tx_type=tx_type,
        trade_date=c.trade_date,
        recorded_at=c.recorded_at,
        legs=tuple(legs),
        sequence=c.sequence,
        trade_time=c.trade_time,
        settle_date=c.settle_date,
        external_ref=c.external_ref,
        note=c.note,
        sleeve=c.sleeve,
        **kw,  # type: ignore[arg-type]
    )


def _positive(name: str, value: Decimal) -> None:
    if value <= 0:
        raise QPValidationError(f"{name} must be positive, got {value}")


def _fee_legs(fee: Decimal, fee_currency: Currency) -> list[Leg]:
    if fee < 0:
        raise QPValidationError(f"fee must be >= 0, got {fee}")
    return [Leg(LegKind.FEE, fee_currency, fee)] if fee else []


def _cash(currency: Currency, amount: Decimal) -> list[Leg]:
    return [Leg(LegKind.CASH, currency, amount)] if amount else []


def _merge_cash(parts: Mapping[Currency, Decimal]) -> list[Leg]:
    return [leg for cur, amt in parts.items() for leg in _cash(cur, amt)]


def trade(
    c: Common,
    side: TxType,
    instrument: InstrumentId,
    currency: Currency,
    quantity: Decimal,
    price: Decimal,
    *,
    fee: Decimal = ZERO,
    fee_currency: Currency | None = None,
    fx_rate_try: Decimal | None = None,
    corporate_action: CorporateAction | None = None,
) -> Transaction:
    """buy, sell or rights_subscription. ``quantity`` is positive for every side."""
    if side not in (TxType.BUY, TxType.SELL, TxType.RIGHTS_SUBSCRIPTION):
        raise QPValidationError(f"trade() does not build {side}")
    _positive("quantity", quantity)
    if price < 0:
        raise QPValidationError("price must be >= 0")
    fee_cur = fee_currency or currency
    value = quantity * price
    sign = -1 if side is TxType.SELL else 1
    cash: dict[Currency, Decimal] = {currency: -sign * value}
    cash[fee_cur] = cash.get(fee_cur, ZERO) - fee
    legs = [
        Leg(LegKind.POSITION, currency, value, instrument, sign * quantity),
        *_fee_legs(fee, fee_cur),
        *_merge_cash(cash),
    ]
    return _tx(c, side, legs, fx_rate_try=fx_rate_try, corporate_action=corporate_action)


def opening_position(
    c: Common,
    instrument: InstrumentId,
    currency: Currency,
    quantity: Decimal,
    total_cost: Decimal,
    *,
    fx_rate_try: Decimal | None = None,
) -> Transaction:
    """A position held before the ledger starts, with its total cost (fees included)."""
    _positive("quantity", quantity)
    if total_cost < 0:
        raise QPValidationError("total_cost must be >= 0")
    leg = Leg(LegKind.POSITION, currency, total_cost, instrument, quantity)
    return _tx(c, TxType.OPENING_BALANCE, [leg], fx_rate_try=fx_rate_try)


def opening_cash(c: Common, currency: Currency, amount: Decimal) -> Transaction:
    _positive("amount", amount)
    return _tx(c, TxType.OPENING_BALANCE, _cash(currency, amount))


def cash_flow(
    c: Common,
    tx_type: TxType,
    currency: Currency,
    amount: Decimal,
    *,
    withholding: Decimal = ZERO,
    fx_rate_try: Decimal | None = None,
) -> Transaction:
    """deposit, withdrawal, fee, tax, interest. ``amount`` is positive; interest is gross."""
    _positive("amount", amount)
    if tx_type in (TxType.DEPOSIT,):
        legs = _cash(currency, amount)
    elif tx_type is TxType.WITHDRAWAL:
        legs = _cash(currency, -amount)
    elif tx_type is TxType.FEE:
        legs = [Leg(LegKind.FEE, currency, amount), *_cash(currency, -amount)]
    elif tx_type is TxType.TAX:
        legs = [Leg(LegKind.TAX, currency, amount), *_cash(currency, -amount)]
    elif tx_type is TxType.INTEREST:
        if not (ZERO <= withholding < amount):
            raise QPValidationError("interest withholding must be in [0, gross)")
        legs = [
            Leg(LegKind.INCOME, currency, amount),
            *([Leg(LegKind.TAX, currency, withholding)] if withholding else []),
            *_cash(currency, amount - withholding),
        ]
    else:
        raise QPValidationError(f"cash_flow() does not build {tx_type}")
    return _tx(c, tx_type, legs, fx_rate_try=fx_rate_try)


def fx_conversion(
    c: Common, sold: Currency, sold_amount: Decimal, bought: Currency, bought_amount: Decimal
) -> Transaction:
    _positive("sold amount", sold_amount)
    _positive("bought amount", bought_amount)
    if sold == bought:
        raise QPValidationError("fx_conversion needs two different currencies")
    legs = [Leg(LegKind.CASH, sold, -sold_amount), Leg(LegKind.CASH, bought, bought_amount)]
    fx = None
    if bought == TRY:
        fx = bought_amount / sold_amount
    elif sold == TRY:
        fx = sold_amount / bought_amount
    return _tx(c, TxType.FX_CONVERSION, legs, fx_rate_try=fx)


def securities_transfer(
    c: Common,
    direction: TxType,
    transfer_id: str,
    instrument: InstrumentId,
    currency: Currency,
    quantity: Decimal,
) -> Transaction:
    """transfer_out / transfer_in between own accounts; cost basis travels with the shares.

    The position leg value is 0: the engine carries the actual cost from the transfer_out.
    """
    _positive("quantity", quantity)
    if direction not in (TxType.TRANSFER_OUT, TxType.TRANSFER_IN):
        raise QPValidationError(f"securities_transfer() does not build {direction}")
    sign = -1 if direction is TxType.TRANSFER_OUT else 1
    leg = Leg(LegKind.POSITION, currency, ZERO, instrument, sign * quantity)
    return _tx(c, direction, [leg], transfer_id=transfer_id)


def cash_transfer(
    c: Common, direction: TxType, transfer_id: str, currency: Currency, amount: Decimal
) -> Transaction:
    _positive("amount", amount)
    if direction not in (TxType.TRANSFER_OUT, TxType.TRANSFER_IN):
        raise QPValidationError(f"cash_transfer() does not build {direction}")
    sign = -1 if direction is TxType.TRANSFER_OUT else 1
    return _tx(c, direction, _cash(currency, sign * amount), transfer_id=transfer_id)


def share_count_action(
    c: Common, action: CorporateAction, currency: Currency, quantity_received: Decimal
) -> Transaction:
    """split / bonus_issue / stock_dividend: shares received (negative for reverse splits)."""
    tx_type = TxType(action.action_type.value)
    leg = Leg(LegKind.POSITION, currency, ZERO, action.instrument, quantity_received)
    return _tx(c, tx_type, [leg], corporate_action=action)


def dividend_amounts(
    eligible_quantity: Decimal,
    amount_per_share: Decimal,
    withholding_rate: Decimal,
    currency: Currency,
) -> tuple[Money, Money, Money]:
    """(gross, withholding, net), each rounded half-even to the currency's minor unit."""
    if not (ZERO <= withholding_rate <= 1):
        raise QPValidationError("withholding_rate must be a fraction in [0, 1]")
    gross = Money(eligible_quantity * amount_per_share, currency).quantize()
    withholding = Money(gross.amount * withholding_rate, currency).quantize()
    return gross, withholding, gross - withholding


def cash_dividend(
    c: Common,
    action: CorporateAction,
    gross: Decimal,
    withholding: Decimal,
    *,
    fx_rate_try: Decimal | None = None,
) -> Transaction:
    """A cash dividend as received: gross, tax withheld, net cash (= gross - withholding)."""
    assert action.currency is not None
    _positive("gross", gross)
    if not (ZERO <= withholding <= gross):
        raise QPValidationError("withholding must be in [0, gross]")
    cur = action.currency
    legs = [
        Leg(LegKind.INCOME, cur, gross),
        *([Leg(LegKind.TAX, cur, withholding)] if withholding else []),
        *_cash(cur, gross - withholding),
    ]
    return _tx(c, TxType.CASH_DIVIDEND, legs, corporate_action=action, fx_rate_try=fx_rate_try)


def merger(
    c: Common,
    action: CorporateAction,
    old_currency: Currency,
    old_quantity: Decimal,
    new_currency: Currency,
    new_quantity: Decimal,
) -> Transaction:
    assert action.new_instrument is not None
    _positive("old quantity", old_quantity)
    _positive("new quantity", new_quantity)
    legs = [
        Leg(LegKind.POSITION, old_currency, ZERO, action.instrument, -old_quantity),
        Leg(LegKind.POSITION, new_currency, ZERO, action.new_instrument, new_quantity),
    ]
    return _tx(c, TxType.MERGER, legs, corporate_action=action)


def delisting(
    c: Common,
    action: CorporateAction,
    currency: Currency,
    quantity: Decimal,
    proceeds: Decimal = ZERO,
    *,
    fx_rate_try: Decimal | None = None,
) -> Transaction:
    _positive("quantity", quantity)
    if proceeds < 0:
        raise QPValidationError("proceeds must be >= 0")
    legs = [
        Leg(LegKind.POSITION, currency, ZERO, action.instrument, -quantity),
        *_cash(currency, proceeds),
    ]
    return _tx(c, TxType.DELISTING, legs, corporate_action=action, fx_rate_try=fx_rate_try)


def reversal(c: Common, reverses_id: int) -> Transaction:
    """Cancel transaction ``reverses_id``: both are ignored from then on (rule 13)."""
    return _tx(c, TxType.REVERSAL, [], reverses_id=reverses_id)
