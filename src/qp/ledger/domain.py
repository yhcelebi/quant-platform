"""Ledger domain types: accounts, transactions and their legs (ADR 0005).

The ledger stores facts only. A transaction is a header (type, dates, account, FX rate,
links) plus legs, the money and quantity movements as they appear on a statement:

- ``position``: an instrument quantity change (signed) and its gross value (>= 0) in the
  instrument currency: quantity x price for trades, total cost for opening balances,
  0 for share-count corporate actions.
- ``cash``: a signed cash movement in one currency.
- ``fee``, ``tax``, ``income``: non-negative amounts (fees paid, tax withheld, gross income).

Positions, average cost, FIFO lots and realized P&L are derived by :mod:`qp.ledger.engine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from qp.core import TRY, Currency, InstrumentId, QPError, QPValidationError, ensure_utc
from qp.reference.domain import CorporateAction, CorporateActionType


class LedgerError(QPError):
    """The ledger facts are inconsistent (oversell, unmatched transfer, bad legs, ...)."""


class AccountType(StrEnum):
    BROKERAGE = "brokerage"
    BANK = "bank"
    CRYPTO_EXCHANGE = "crypto_exchange"
    PENSION = "pension"
    OTHER = "other"


class AccountPurpose(StrEnum):
    LONG_TERM = "long_term"
    TRADING = "trading"


class TxType(StrEnum):
    OPENING_BALANCE = "opening_balance"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    BUY = "buy"
    SELL = "sell"
    FEE = "fee"
    INTEREST = "interest"
    TAX = "tax"
    FX_CONVERSION = "fx_conversion"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    SPLIT = "split"
    BONUS_ISSUE = "bonus_issue"
    STOCK_DIVIDEND = "stock_dividend"
    RIGHTS_SUBSCRIPTION = "rights_subscription"
    CASH_DIVIDEND = "cash_dividend"
    MERGER = "merger"
    DELISTING = "delisting"
    REVERSAL = "reversal"


# Ledger transaction type -> the corporate action it must be linked to.
CORPORATE_ACTION_OF: dict[TxType, CorporateActionType] = {
    TxType.SPLIT: CorporateActionType.SPLIT,
    TxType.BONUS_ISSUE: CorporateActionType.BONUS_ISSUE,
    TxType.STOCK_DIVIDEND: CorporateActionType.STOCK_DIVIDEND,
    TxType.RIGHTS_SUBSCRIPTION: CorporateActionType.RIGHTS_ISSUE,
    TxType.CASH_DIVIDEND: CorporateActionType.CASH_DIVIDEND,
    TxType.MERGER: CorporateActionType.MERGER,
    TxType.DELISTING: CorporateActionType.DELISTING,
}

EXTERNAL_FLOW_TYPES = frozenset({TxType.DEPOSIT, TxType.WITHDRAWAL})

# Types whose values are converted to TRY (cost basis, realized P&L, income) and therefore
# need the trade-time rate when the main currency is not TRY. Others move no value that
# needs converting (transfers carry cost; share-count actions and mergers carry cost).
FX_REQUIRED_TYPES = frozenset(
    {
        TxType.BUY,
        TxType.SELL,
        TxType.RIGHTS_SUBSCRIPTION,
        TxType.CASH_DIVIDEND,
        TxType.INTEREST,
    }
)
TRANSFER_TYPES = frozenset({TxType.TRANSFER_OUT, TxType.TRANSFER_IN})


class LegKind(StrEnum):
    POSITION = "position"
    CASH = "cash"
    FEE = "fee"
    TAX = "tax"
    INCOME = "income"


@dataclass(frozen=True, slots=True)
class Account:
    code: str
    name: str
    account_type: AccountType
    purpose: AccountPurpose
    base_currency: Currency
    broker: str = ""


@dataclass(frozen=True, slots=True)
class Leg:
    kind: LegKind
    currency: Currency
    amount: Decimal
    instrument: InstrumentId | None = None
    quantity: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise QPValidationError(f"leg amount must be a finite Decimal: {self.amount!r}")
        if self.kind is LegKind.POSITION:
            if self.instrument is None or self.quantity is None:
                raise QPValidationError("position leg needs instrument and quantity")
            if not isinstance(self.quantity, Decimal) or not self.quantity.is_finite():
                raise QPValidationError("position leg quantity must be a finite Decimal")
            if self.quantity == 0:
                raise QPValidationError("position leg quantity cannot be zero")
            if self.amount < 0:
                raise QPValidationError("position leg value must be >= 0")
        else:
            if self.instrument is not None or self.quantity is not None:
                raise QPValidationError(f"{self.kind} leg cannot carry instrument or quantity")
            if self.kind is not LegKind.CASH and self.amount < 0:
                raise QPValidationError(f"{self.kind} leg amount must be >= 0")
            if self.kind is LegKind.CASH and self.amount == 0:
                raise QPValidationError("cash leg amount cannot be zero")


@dataclass(frozen=True, slots=True)
class Transaction:
    account: str
    tx_type: TxType
    trade_date: date
    recorded_at: datetime
    legs: tuple[Leg, ...] = ()
    sequence: int = 0
    trade_time: datetime | None = None
    settle_date: date | None = None
    fx_rate_try: Decimal | None = None
    corporate_action: CorporateAction | None = None
    transfer_id: str | None = None
    reverses_id: int | None = None
    external_ref: str | None = None
    note: str = ""
    sleeve: str = "main"
    id: int | None = None
    _main_currency: Currency | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "recorded_at", ensure_utc(self.recorded_at))
        if self.trade_time is not None:
            object.__setattr__(self, "trade_time", ensure_utc(self.trade_time))
        if self.fx_rate_try is not None and self.fx_rate_try <= 0:
            raise QPValidationError("fx_rate_try must be positive")
        expected_ca = CORPORATE_ACTION_OF.get(self.tx_type)
        if expected_ca is not None:
            ca = self.corporate_action
            if ca is None or ca.action_type is not expected_ca:
                raise QPValidationError(
                    f"{self.tx_type} must link a {expected_ca} corporate action"
                )
        elif self.corporate_action is not None:
            raise QPValidationError(f"{self.tx_type} cannot link a corporate action")
        if self.tx_type in TRANSFER_TYPES and not self.transfer_id:
            raise QPValidationError(f"{self.tx_type} needs a transfer_id")
        if self.tx_type is TxType.REVERSAL:
            if self.reverses_id is None or self.legs:
                raise QPValidationError("a reversal references reverses_id and has no legs")
        else:
            if self.reverses_id is not None:
                raise QPValidationError("only reversal transactions may set reverses_id")
            if not self.legs:
                raise QPValidationError(f"{self.tx_type} needs at least one leg")
        object.__setattr__(self, "_main_currency", self._derive_main_currency())
        main = self._main_currency
        if main is not None and main != TRY and self.fx_rate_try is None and self._needs_fx():
            raise QPValidationError(
                f"{self.tx_type} in {main} needs fx_rate_try (TRY per 1 {main})"
            )

    def _needs_fx(self) -> bool:
        if self.tx_type in FX_REQUIRED_TYPES:
            return True
        if self.tx_type is TxType.OPENING_BALANCE:
            return bool(self.legs_of(LegKind.POSITION))
        if self.tx_type is TxType.DELISTING:
            return bool(self.legs_of(LegKind.CASH))
        return False

    def _derive_main_currency(self) -> Currency | None:
        for leg in self.legs:
            if leg.kind is LegKind.POSITION:
                return leg.currency
        for kind in (LegKind.INCOME, LegKind.CASH):
            for leg in self.legs:
                if leg.kind is kind:
                    return leg.currency
        return None

    @property
    def main_currency(self) -> Currency | None:
        """Currency of the position leg, else of income, else of the first cash leg."""
        return self._main_currency

    @property
    def fx(self) -> Decimal:
        """TRY per 1 unit of the main currency (1 for TRY). Only valid when a rate exists."""
        if self.main_currency is None or self.main_currency == TRY:
            return Decimal(1)
        if self.fx_rate_try is None:
            raise LedgerError(f"{self.describe()}: no fx_rate_try for {self.main_currency}")
        return self.fx_rate_try

    def legs_of(self, kind: LegKind) -> list[Leg]:
        return [leg for leg in self.legs if leg.kind is kind]

    def describe(self) -> str:
        ref = f" ref={self.external_ref}" if self.external_ref else ""
        ident = f"#{self.id}" if self.id is not None else f"seq {self.sequence}"
        return f"{self.tx_type} {self.account} {self.trade_date} ({ident}{ref})"
