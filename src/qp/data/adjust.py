"""Adjusted prices, derived from unadjusted prices and our own ``ref.corporate_actions``.

Point in time: an adjustment uses only actions with ``known_time <= as_of``, so a series
"as of t" never reflects a split announced after t (DOMAIN.md, corporate actions).

- ``splits`` adjusts for share-count actions (split, bonus issue, stock dividend): every
  price before the ex-date is multiplied by 1/(1+k) or 1/r.
- ``total_return`` also adjusts for cash dividends: prices before the ex-date are multiplied
  by 1 - D / P, where D is the gross dividend per share and P the last close before the
  ex-date (in the same currency). A dividend with no prior price in the series is an error.

Unadjusted prices are never modified; this returns new points.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from qp.core import QPError, ensure_utc
from qp.data.market import PricePoint
from qp.reference.domain import (
    SHARE_COUNT_ACTIONS,
    CorporateAction,
    CorporateActionType,
    price_adjustment_factor,
)

ONE = Decimal(1)


class AdjustmentKind(StrEnum):
    SPLITS = "splits"
    TOTAL_RETURN = "total_return"


class AdjustmentError(QPError):
    pass


def adjust(
    prices: Sequence[PricePoint],
    actions: Sequence[CorporateAction],
    *,
    as_of: datetime,
    kind: AdjustmentKind = AdjustmentKind.SPLITS,
) -> list[PricePoint]:
    """Adjusted copies of one instrument's ``prices`` (any order; returned by date)."""
    series = sorted(prices, key=lambda p: p.valid_date)
    if not series:
        return []
    instrument = series[0].instrument
    if any(p.instrument != instrument for p in series):
        raise AdjustmentError("adjust one instrument at a time")
    as_of = ensure_utc(as_of)
    factors: list[tuple[CorporateAction, Decimal]] = []
    for a in sorted(actions, key=lambda a: a.ex_date):
        if a.instrument != instrument or a.known_time > as_of:
            continue
        if a.action_type in SHARE_COUNT_ACTIONS:
            factors.append((a, price_adjustment_factor(a)))
        elif (
            a.action_type is CorporateActionType.CASH_DIVIDEND
            and kind is AdjustmentKind.TOTAL_RETURN
        ):
            factors.append((a, _dividend_factor(a, series)))
    out = []
    for p in series:
        f = ONE
        for a, factor in factors:
            if p.valid_date < a.ex_date:
                f *= factor
        out.append(
            PricePoint(
                p.instrument,
                p.valid_date,
                p.price * f,
                p.currency,
                f"{p.source}+adj:{kind.value}",
                max(p.known_time, *(a.known_time for a, _ in factors if p.valid_date < a.ex_date))
                if f != ONE
                else p.known_time,
            )
        )
    return out


def _dividend_factor(action: CorporateAction, series: Sequence[PricePoint]) -> Decimal:
    assert action.amount_per_share is not None
    before = [p for p in series if p.valid_date < action.ex_date]
    if not before:
        raise AdjustmentError(
            f"{action.instrument} dividend ex {action.ex_date}: no price before the ex-date"
        )
    close = before[-1]
    if action.currency is not None and action.currency != close.currency:
        raise AdjustmentError(
            f"{action.instrument} dividend in {action.currency}, prices in {close.currency}"
        )
    if close.price <= action.amount_per_share:
        raise AdjustmentError(
            f"{action.instrument} dividend {action.amount_per_share} >= close {close.price}"
        )
    return ONE - action.amount_per_share / close.price
