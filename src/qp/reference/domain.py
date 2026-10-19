"""Reference-data domain types: instruments, accounts' counterparties and corporate actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from qp.core import Currency, InstrumentId, QPValidationError, ensure_utc


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    FUND = "fund"
    BOND = "bond"
    CRYPTO = "crypto"
    COMMODITY = "commodity"
    OTHER = "other"


class CorporateActionType(StrEnum):
    SPLIT = "split"
    BONUS_ISSUE = "bonus_issue"
    STOCK_DIVIDEND = "stock_dividend"
    RIGHTS_ISSUE = "rights_issue"
    CASH_DIVIDEND = "cash_dividend"
    MERGER = "merger"
    DELISTING = "delisting"


# Actions whose `ratio` multiplies the share count: new_qty = old_qty * factor.
#   split ratio r       -> factor r        (2-for-1: r = 2; 1-for-10 reverse split: r = 0.1)
#   bonus / stock div k -> factor (1 + k)  (100% bonus: k = 1)
SHARE_COUNT_ACTIONS = frozenset(
    {CorporateActionType.SPLIT, CorporateActionType.BONUS_ISSUE, CorporateActionType.STOCK_DIVIDEND}
)


@dataclass(frozen=True, slots=True)
class Venue:
    code: str
    name: str
    timezone: str
    country: str | None
    currency: Currency | None


@dataclass(frozen=True, slots=True)
class Instrument:
    id: InstrumentId
    currency: Currency
    asset_class: AssetClass
    name: str = ""
    isin: str | None = None


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """An instrument-level corporate action, as a point-in-time fact.

    Parameters by type:
      split, bonus_issue, stock_dividend: ratio (see SHARE_COUNT_ACTIONS)
      rights_issue: ratio (new shares per held share), subscription_price, currency
      cash_dividend: amount_per_share (gross), currency
      merger: ratio (new shares per old share), new_instrument
      delisting: no parameters
    """

    instrument: InstrumentId
    action_type: CorporateActionType
    ex_date: date
    source: str
    known_time: datetime
    ratio: Decimal | None = None
    amount_per_share: Decimal | None = None
    currency: Currency | None = None
    subscription_price: Decimal | None = None
    new_instrument: InstrumentId | None = None
    record_date: date | None = None
    pay_date: date | None = None
    note: str = ""
    id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "known_time", ensure_utc(self.known_time))
        t = self.action_type
        needs_ratio = t in SHARE_COUNT_ACTIONS or t in (
            CorporateActionType.RIGHTS_ISSUE,
            CorporateActionType.MERGER,
        )
        if needs_ratio and (self.ratio is None or self.ratio <= 0):
            raise QPValidationError(f"{t} needs a positive ratio")
        if t is CorporateActionType.RIGHTS_ISSUE and (
            self.subscription_price is None or self.subscription_price < 0 or self.currency is None
        ):
            raise QPValidationError("rights_issue needs subscription_price >= 0 and currency")
        if t is CorporateActionType.CASH_DIVIDEND and (
            self.amount_per_share is None or self.amount_per_share <= 0 or self.currency is None
        ):
            raise QPValidationError("cash_dividend needs a positive amount_per_share and currency")
        if t is CorporateActionType.MERGER and self.new_instrument is None:
            raise QPValidationError("merger needs new_instrument")
        if self.pay_date is not None and self.pay_date < self.ex_date:
            raise QPValidationError("pay_date cannot be before ex_date")

    @property
    def share_factor(self) -> Decimal:
        """new_qty / old_qty for share-count actions and mergers."""
        assert self.ratio is not None
        if self.action_type is CorporateActionType.SPLIT:
            return self.ratio
        if self.action_type in (
            CorporateActionType.BONUS_ISSUE,
            CorporateActionType.STOCK_DIVIDEND,
        ):
            return 1 + self.ratio
        if self.action_type is CorporateActionType.MERGER:
            return self.ratio
        raise QPValidationError(f"{self.action_type} has no share factor")

    def same_terms(self, other: CorporateAction) -> bool:
        """True if both describe the same event with the same parameters (ignores provenance)."""
        return (
            self.instrument,
            self.action_type,
            self.ex_date,
            self.ratio,
            self.amount_per_share,
            self.currency,
            self.subscription_price,
            self.new_instrument,
        ) == (
            other.instrument,
            other.action_type,
            other.ex_date,
            other.ratio,
            other.amount_per_share,
            other.currency,
            other.subscription_price,
            other.new_instrument,
        )


def price_adjustment_factor(action: CorporateAction) -> Decimal:
    """Factor applied to prices before the ex-date so they are comparable after it.

    Bonus issue / stock dividend with ratio k: 1 / (1 + k). Split with ratio r: 1 / r.
    Cash dividends need the pre-ex close price: see qp.data.adjust (total-return series).
    """
    if action.action_type in SHARE_COUNT_ACTIONS:
        return 1 / action.share_factor
    raise NotImplementedError(f"price adjustment for {action.action_type} needs price data")
