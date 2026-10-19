from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from qp.core import TRY, USD, InstrumentId
from qp.data.adjust import AdjustmentError, AdjustmentKind, adjust
from qp.data.manual import default_known_time
from qp.data.market import PricePoint
from qp.reference.domain import CorporateAction, CorporateActionType

THY = InstrumentId.parse("THYAO.BIST")
D0 = date(2025, 1, 6)
KNOWN = datetime(2025, 1, 1, tzinfo=UTC)
LATER = datetime(2025, 3, 1, tzinfo=UTC)


def px(day: int, price: str) -> PricePoint:
    d = D0 + timedelta(days=day)
    return PricePoint(THY, d, Decimal(price), TRY, "manual", default_known_time(d))


def action(
    kind: CorporateActionType, day: int, known: datetime = KNOWN, **kw: object
) -> CorporateAction:
    return CorporateAction(THY, kind, D0 + timedelta(days=day), "test", known, **kw)  # type: ignore[arg-type]


SERIES = [px(0, "20"), px(1, "21"), px(2, "10.4"), px(3, "10.6")]


def test_bonus_issue_adjusts_prices_before_ex_date() -> None:
    bonus = action(CorporateActionType.BONUS_ISSUE, 2, ratio=Decimal("1"))  # 100% bedelsiz
    out = adjust(SERIES, [bonus], as_of=LATER)
    assert [p.price for p in out] == [
        Decimal("10"),
        Decimal("10.5"),
        Decimal("10.4"),
        Decimal("10.6"),
    ]
    assert out[0].source == "manual+adj:splits"
    assert out[2].known_time == SERIES[2].known_time  # on/after ex-date: unchanged
    assert [p.price for p in SERIES] == [
        Decimal("20"),
        Decimal("21"),
        Decimal("10.4"),
        Decimal("10.6"),
    ]


def test_adjustment_is_point_in_time() -> None:
    split = action(CorporateActionType.SPLIT, 2, known=LATER, ratio=Decimal("2"))
    before = adjust(SERIES, [split], as_of=LATER - timedelta(seconds=1))
    assert before[0].price == Decimal("20")  # the split was not known yet
    after = adjust(SERIES, [split], as_of=LATER)
    assert after[0].price == Decimal("10")
    assert after[0].known_time == LATER  # an adjusted value is known only once its action is


def test_total_return_includes_cash_dividends() -> None:
    div = action(
        CorporateActionType.CASH_DIVIDEND, 2, amount_per_share=Decimal("1.05"), currency=TRY
    )
    splits_only = adjust(SERIES, [div], as_of=LATER)
    assert splits_only[0].price == Decimal("20")
    tr = adjust(SERIES, [div], as_of=LATER, kind=AdjustmentKind.TOTAL_RETURN)
    factor = 1 - Decimal("1.05") / Decimal("21")  # last close before the ex-date
    assert tr[0].price == Decimal("20") * factor
    assert tr[1].price == Decimal("19.95")  # 21 x 0.95
    assert tr[2].price == Decimal("10.4")


def test_adjustment_errors() -> None:
    early = action(
        CorporateActionType.CASH_DIVIDEND, 0, amount_per_share=Decimal("1"), currency=TRY
    )
    with pytest.raises(AdjustmentError, match="no price before"):
        adjust(SERIES, [early], as_of=LATER, kind=AdjustmentKind.TOTAL_RETURN)
    usd = action(CorporateActionType.CASH_DIVIDEND, 2, amount_per_share=Decimal("1"), currency=USD)
    with pytest.raises(AdjustmentError, match="in USD"):
        adjust(SERIES, [usd], as_of=LATER, kind=AdjustmentKind.TOTAL_RETURN)
    other = PricePoint(InstrumentId.parse("AKBNK.BIST"), D0, Decimal(1), TRY, "m", KNOWN)
    with pytest.raises(AdjustmentError, match="one instrument"):
        adjust([*SERIES, other], [], as_of=LATER)
    assert adjust([], [], as_of=LATER) == []
