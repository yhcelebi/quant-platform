from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qp.core import TRY, USD, Currency, InstrumentId, QPValidationError
from qp.ledger import builders as b
from qp.ledger.domain import LedgerError, Leg, LegKind, Transaction, TxType
from qp.ledger.engine import replay, replay_steps
from qp.reference.domain import CorporateAction, CorporateActionType

THYAO = InstrumentId.parse("THYAO.BIST")
AAPL = InstrumentId.parse("AAPL.NASDAQ")
NEWCO = InstrumentId.parse("NEWCO.BIST")
T0 = datetime(2025, 1, 1, 18, tzinfo=UTC)


def c(day: int, account: str = "A", seq: int = 0, recorded: datetime | None = None) -> b.Common:
    d = date(2025, 1, 1) + timedelta(days=day)
    return b.Common(account, d, recorded or T0 + timedelta(days=day), sequence=seq)


def buy(day: int, qty: str, price: str, fee: str = "0", account: str = "A") -> Transaction:
    return b.trade(
        c(day, account), TxType.BUY, THYAO, TRY, Decimal(qty), Decimal(price), fee=Decimal(fee)
    )


def sell(day: int, qty: str, price: str, fee: str = "0", account: str = "A") -> Transaction:
    return b.trade(
        c(day, account), TxType.SELL, THYAO, TRY, Decimal(qty), Decimal(price), fee=Decimal(fee)
    )


def ca(
    kind: CorporateActionType, day: int, inst: InstrumentId = THYAO, **kw: object
) -> CorporateAction:
    return CorporateAction(inst, kind, date(2025, 1, 1) + timedelta(days=day), "test", T0, **kw)  # type: ignore[arg-type]


# ----------------------------------------------------------------- buys, sells, FIFO
def test_weighted_average_and_fifo_diverge() -> None:
    s = replay([buy(1, "100", "10"), buy(2, "100", "20"), sell(3, "150", "30")])
    r = s.realizations[0]
    assert r.cost == Decimal("2250")  # 150 x 15
    assert r.fifo_cost == Decimal("2000")  # 100 x 10 + 50 x 20
    assert r.realized == Decimal("2250")  # 4500 - 2250
    assert r.realized_fifo == Decimal("2500")  # 4500 - 2000
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == 50
    assert pos.cost == Decimal("750")
    assert pos.fifo_cost == Decimal("1000")


def test_selling_everything_leaves_no_residue() -> None:
    s = replay([buy(1, "3", "10"), sell(2, "1", "11"), sell(3, "2", "12")])
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == pos.cost == pos.cost_try == 0
    assert pos.lots == []
    assert sum(r.cost for r in s.realizations) == Decimal("30")


def test_oversell_is_an_error() -> None:
    with pytest.raises(LedgerError, match="only 10 held"):
        replay([buy(1, "10", "1"), sell(2, "11", "1")])


def test_sell_without_position_is_an_error() -> None:
    with pytest.raises(LedgerError, match=r"no THYAO\.BIST held"):
        replay([sell(1, "1", "1")])


def test_cash_balance_tracks_trades_and_fees() -> None:
    dep = b.cash_flow(c(0), TxType.DEPOSIT, TRY, Decimal("5000"))
    s = replay([dep, buy(1, "100", "10", "1"), sell(2, "50", "30", "1.5")])
    assert s.cash[("A", TRY)] == Decimal("5000") - Decimal("1001") + Decimal("1498.5")
    assert [f.amount for f in s.flows] == [Decimal("5000")]
    assert [m.amount for _, m in s.fees] == [Decimal("1"), Decimal("1.5")]


def test_foreign_trade_tracks_try_cost_and_try_fee() -> None:
    tx = b.trade(
        c(1),
        TxType.BUY,
        AAPL,
        USD,
        Decimal("10"),
        Decimal("200"),
        fee=Decimal("40"),
        fee_currency=TRY,
        fx_rate_try=Decimal("40"),
    )
    s = replay([tx])
    pos = s.position("A", AAPL)
    assert pos is not None
    assert pos.cost == Decimal("2001")  # 2000 USD + 40 TRY / 40
    assert pos.cost_try == Decimal("80040")
    assert s.cash[("A", USD)] == Decimal("-2000")
    assert s.cash[("A", TRY)] == Decimal("-40")


def test_realized_try_uses_both_trade_rates() -> None:
    s = replay(
        [
            b.trade(
                c(1), TxType.BUY, AAPL, USD, Decimal("1"), Decimal("100"), fx_rate_try=Decimal("30")
            ),
            b.trade(
                c(2),
                TxType.SELL,
                AAPL,
                USD,
                Decimal("1"),
                Decimal("100"),
                fx_rate_try=Decimal("40"),
            ),
        ]
    )
    r = s.realizations[0]
    assert r.realized == 0
    assert r.realized_try == Decimal("1000")  # pure FX gain


def test_foreign_trade_requires_fx_rate() -> None:
    with pytest.raises(QPValidationError, match=r"needs fx_rate_try"):
        b.trade(c(1), TxType.BUY, AAPL, USD, Decimal("1"), Decimal("1"))


def test_currency_mismatch_with_existing_position() -> None:
    other = b.trade(
        c(2), TxType.BUY, THYAO, USD, Decimal("1"), Decimal("1"), fx_rate_try=Decimal("40")
    )
    with pytest.raises(LedgerError, match="held in TRY"):
        replay([buy(1, "1", "1"), other])


def test_tampered_cash_legs_are_detected() -> None:
    good = buy(1, "10", "10", "1")
    legs = tuple(
        Leg(LegKind.CASH, TRY, Decimal("-100")) if leg.kind is LegKind.CASH else leg
        for leg in good.legs
    )
    bad = Transaction(good.account, good.tx_type, good.trade_date, good.recorded_at, legs)
    with pytest.raises(LedgerError, match="cash legs"):
        replay([bad])


# ----------------------------------------------------------------- ordering and time
def test_same_day_corporate_action_applies_before_trades() -> None:
    bonus = ca(CorporateActionType.BONUS_ISSUE, 2, ratio=Decimal("1"))
    txs = [
        buy(1, "100", "10"),
        sell(2, "150", "6"),
        b.share_count_action(c(2), bonus, TRY, Decimal("100")),
    ]
    s = replay(txs)  # the sell of 150 only works after the bonus doubles 100 -> 200
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == 50


def test_as_of_filters_by_trade_date() -> None:
    s = replay([buy(1, "10", "1"), buy(5, "10", "1")], as_of=date(2025, 1, 3))
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == 10


def test_backdated_entry_changes_history_but_known_at_reproduces_old_view() -> None:
    first = buy(1, "10", "10")
    late = b.trade(
        c(2, recorded=T0 + timedelta(days=30)), TxType.BUY, THYAO, TRY, Decimal("10"), Decimal("20")
    )
    now = replay([first, late])
    then = replay([first, late], known_at=T0 + timedelta(days=10))
    assert now.position("A", THYAO).quantity == 20  # type: ignore[union-attr]
    assert then.position("A", THYAO).quantity == 10  # type: ignore[union-attr]


def test_timed_trades_order_before_untimed_on_same_day() -> None:
    timed = b.trade(
        b.Common(
            "A",
            date(2025, 1, 3),
            T0 + timedelta(days=9),
            trade_time=datetime(2025, 1, 3, 7, tzinfo=UTC),
        ),
        TxType.BUY,
        THYAO,
        TRY,
        Decimal("5"),
        Decimal("1"),
    )
    untimed_sell = b.trade(
        c(2), TxType.SELL, THYAO, TRY, Decimal("5"), Decimal("1")
    )  # recorded earlier
    s = replay([untimed_sell, timed])
    assert s.position("A", THYAO).quantity == 0  # type: ignore[union-attr]


# ----------------------------------------------------------------- reversals
def _with_id(tx: Transaction, tx_id: int) -> Transaction:
    from dataclasses import replace

    return replace(tx, id=tx_id)


def test_reversal_cancels_original() -> None:
    a = _with_id(buy(1, "10", "10"), 1)
    wrong = _with_id(buy(2, "100", "99"), 2)
    rev = _with_id(b.reversal(c(3), 2), 3)
    fixed = _with_id(  # the correction is recorded together with the reversal, on day 3
        b.trade(
            c(2, recorded=T0 + timedelta(days=3)),
            TxType.BUY,
            THYAO,
            TRY,
            Decimal("10"),
            Decimal("12"),
        ),
        4,
    )
    s = replay([a, wrong, rev, fixed])
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == 20
    assert pos.cost == Decimal("220")
    before = replay([a, wrong, rev, fixed], known_at=T0 + timedelta(days=2))
    assert before.position("A", THYAO).quantity == 110  # type: ignore[union-attr]


def test_reversal_errors() -> None:
    a = _with_id(buy(1, "10", "10"), 1)
    with pytest.raises(LedgerError, match="unknown transaction"):
        replay([a, _with_id(b.reversal(c(2), 99), 2)])
    with pytest.raises(LedgerError, match="reversed twice"):
        replay([a, _with_id(b.reversal(c(2), 1), 2), _with_id(b.reversal(c(3), 1), 3)])
    with pytest.raises(LedgerError, match="cannot reverse a reversal"):
        replay([a, _with_id(b.reversal(c(2), 1), 2), _with_id(b.reversal(c(3), 2), 3)])


def test_reversal_shape_is_validated() -> None:
    with pytest.raises(QPValidationError):
        Transaction("A", TxType.REVERSAL, date(2025, 1, 1), T0)
    with pytest.raises(QPValidationError):
        Transaction("A", TxType.BUY, date(2025, 1, 1), T0, reverses_id=1)


# ----------------------------------------------------------------- corporate actions
def test_split_and_reverse_split_keep_cost() -> None:
    split = ca(CorporateActionType.SPLIT, 2, ratio=Decimal("2"))
    rev = ca(CorporateActionType.SPLIT, 3, ratio=Decimal("0.1"))
    s = replay(
        [
            buy(1, "100", "10"),
            b.share_count_action(c(2), split, TRY, Decimal("100")),
            b.share_count_action(c(3), rev, TRY, Decimal("-180")),
        ]
    )
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == 20
    assert pos.cost == Decimal("1000")
    assert sum(lot.quantity for lot in pos.lots) == 20


def test_share_count_mismatch() -> None:
    bonus = ca(CorporateActionType.BONUS_ISSUE, 2, ratio=Decimal("0.5"))
    with pytest.raises(LedgerError, match="expected 50"):
        replay([buy(1, "100", "1"), b.share_count_action(c(2), bonus, TRY, Decimal("40"))])
    # fractional rounding by the broker is a warning, not an error
    odd = ca(CorporateActionType.BONUS_ISSUE, 2, ratio=Decimal("0.5"))
    s = replay([buy(1, "101", "1"), b.share_count_action(c(2), odd, TRY, Decimal("50"))])
    assert s.warnings
    assert "rounding" in s.warnings[0]


def test_share_count_action_without_holding() -> None:
    bonus = ca(CorporateActionType.BONUS_ISSUE, 2, ratio=Decimal("1"))
    with pytest.raises(LedgerError, match=r"no THYAO\.BIST held"):
        replay([b.share_count_action(c(2), bonus, TRY, Decimal("1"))])


def test_dividend_uses_holdings_before_ex_date() -> None:
    div = ca(
        CorporateActionType.CASH_DIVIDEND,
        3,
        amount_per_share=Decimal("0.5"),
        currency=TRY,
        pay_date=date(2025, 1, 10),
    )
    txs = [
        buy(1, "100", "10"),
        buy(3, "900", "10"),  # bought on the ex-date: not eligible
        b.cash_dividend(c(9), div, Decimal("50"), Decimal("5")),
    ]
    s = replay(txs)
    ev = s.income[0]
    assert ev.eligible_quantity == 100
    assert ev.net == Decimal("45")
    assert s.cash[("A", TRY)] == Decimal("-10000") + Decimal("45")


def test_dividend_gross_mismatch() -> None:
    div = ca(CorporateActionType.CASH_DIVIDEND, 2, amount_per_share=Decimal("0.5"), currency=TRY)
    with pytest.raises(LedgerError, match="eligible"):
        replay([buy(1, "100", "10"), b.cash_dividend(c(2), div, Decimal("60"), Decimal("0"))])


def test_foreign_dividend_needs_fx() -> None:
    div = ca(
        CorporateActionType.CASH_DIVIDEND,
        2,
        inst=AAPL,
        amount_per_share=Decimal("0.25"),
        currency=USD,
    )
    with pytest.raises(QPValidationError, match="fx_rate_try"):
        b.cash_dividend(c(3), div, Decimal("2.5"), Decimal("0.38"))


def test_rights_subscription_adds_cost_and_checks_price() -> None:
    rights = ca(
        CorporateActionType.RIGHTS_ISSUE,
        2,
        ratio=Decimal("0.5"),
        subscription_price=Decimal("1"),
        currency=TRY,
    )
    sub = b.trade(
        c(3),
        TxType.RIGHTS_SUBSCRIPTION,
        THYAO,
        TRY,
        Decimal("50"),
        Decimal("1"),
        corporate_action=rights,
    )
    s = replay([buy(1, "100", "10"), sub])
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == 150
    assert pos.cost == Decimal("1050")
    wrong_price = b.trade(
        c(3),
        TxType.RIGHTS_SUBSCRIPTION,
        THYAO,
        TRY,
        Decimal("50"),
        Decimal("2"),
        corporate_action=rights,
    )
    with pytest.raises(LedgerError, match="subscription price"):
        replay([buy(1, "100", "10"), wrong_price])


def test_merger_carries_cost_and_lots() -> None:
    m = ca(CorporateActionType.MERGER, 3, ratio=Decimal("0.5"), new_instrument=NEWCO)
    s = replay([buy(1, "100", "10"), b.merger(c(3), m, TRY, Decimal("100"), TRY, Decimal("50"))])
    assert s.position("A", THYAO).quantity == 0  # type: ignore[union-attr]
    new = s.position("A", NEWCO)
    assert new is not None
    assert new.quantity == 50
    assert new.cost == Decimal("1000")
    assert new.lots[0].acquired == date(2025, 1, 2)


def test_merger_must_exchange_whole_position() -> None:
    m = ca(CorporateActionType.MERGER, 3, ratio=Decimal("0.5"), new_instrument=NEWCO)
    with pytest.raises(LedgerError, match="whole"):
        replay([buy(1, "100", "10"), b.merger(c(3), m, TRY, Decimal("60"), TRY, Decimal("30"))])


def test_delisting_realizes_loss() -> None:
    d = ca(CorporateActionType.DELISTING, 3)
    s = replay([buy(1, "100", "10"), b.delisting(c(3), d, TRY, Decimal("100"), Decimal("50"))])
    r = s.realizations[0]
    assert r.realized == Decimal("-950")
    assert r.tx_type is TxType.DELISTING
    assert s.position("A", THYAO).quantity == 0  # type: ignore[union-attr]


def test_corporate_action_link_is_required() -> None:
    with pytest.raises(QPValidationError, match="must link"):
        Transaction(
            "A",
            TxType.SPLIT,
            date(2025, 1, 1),
            T0,
            (Leg(LegKind.POSITION, TRY, Decimal(0), THYAO, Decimal(1)),),
        )


# ----------------------------------------------------------------- transfers, cash, opening
def test_securities_transfer_carries_cost_and_lot_dates() -> None:
    out = b.securities_transfer(c(5, "A"), TxType.TRANSFER_OUT, "t1", THYAO, TRY, Decimal("60"))
    inn = b.securities_transfer(c(6, "B"), TxType.TRANSFER_IN, "t1", THYAO, TRY, Decimal("60"))
    s = replay([buy(1, "100", "10"), out, inn])
    a, bb = s.position("A", THYAO), s.position("B", THYAO)
    assert a is not None
    assert bb is not None
    assert a.quantity == 40
    assert a.cost == Decimal("400")
    assert bb.quantity == 60
    assert bb.cost == Decimal("600")
    assert bb.lots[0].acquired == date(2025, 1, 2)


def test_transfer_in_without_out_and_in_transit_warning() -> None:
    inn = b.securities_transfer(c(6, "B"), TxType.TRANSFER_IN, "t9", THYAO, TRY, Decimal("1"))
    with pytest.raises(LedgerError, match="no earlier transfer_out"):
        replay([inn])
    out = b.cash_transfer(c(2), TxType.TRANSFER_OUT, "c1", TRY, Decimal("10"))
    s = replay([b.cash_flow(c(1), TxType.DEPOSIT, TRY, Decimal("10")), out])
    assert any("no matching transfer_in" in w for w in s.warnings)


def test_cash_transfer_moves_cash_without_external_flow() -> None:
    txs = [
        b.cash_flow(c(1), TxType.DEPOSIT, TRY, Decimal("100")),
        b.cash_transfer(c(2, "A"), TxType.TRANSFER_OUT, "c1", TRY, Decimal("40")),
        b.cash_transfer(c(2, "B"), TxType.TRANSFER_IN, "c1", TRY, Decimal("40")),
    ]
    s = replay(txs)
    assert s.cash[("A", TRY)] == 60
    assert s.cash[("B", TRY)] == 40
    assert len(s.flows) == 1


def test_fx_conversion_and_interest_and_fees() -> None:
    txs = [
        b.cash_flow(c(1), TxType.DEPOSIT, TRY, Decimal("4000")),
        b.fx_conversion(c(2), TRY, Decimal("4000"), USD, Decimal("100")),
        b.cash_flow(
            c(3),
            TxType.INTEREST,
            USD,
            Decimal("10"),
            withholding=Decimal("1"),
            fx_rate_try=Decimal("41"),
        ),
        b.cash_flow(c(4), TxType.FEE, USD, Decimal("2")),
        b.cash_flow(c(5), TxType.TAX, USD, Decimal("3")),
        b.cash_flow(c(6), TxType.WITHDRAWAL, USD, Decimal("4")),
    ]
    s = replay(txs)
    assert s.cash_balances() == {("A", USD): Decimal("100")}
    assert s.income[0].net == Decimal("9")
    assert s.income[0].fx_rate_try == Decimal("41")
    assert [f.amount for f in s.flows] == [Decimal("4000"), Decimal("-4")]


def test_opening_balances() -> None:
    txs = [
        b.opening_position(
            c(0), AAPL, USD, Decimal("5"), Decimal("500"), fx_rate_try=Decimal("30")
        ),
        b.opening_cash(c(0), TRY, Decimal("1000")),
    ]
    s = replay(txs)
    pos = s.position("A", AAPL)
    assert pos is not None
    assert pos.cost_try == Decimal("15000")
    assert s.cash[("A", TRY)] == Decimal("1000")
    assert not s.flows


# ----------------------------------------------------------------- properties
quantities = st.integers(min_value=1, max_value=10_000).map(Decimal)
prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("10000"), places=2)


@given(st.lists(st.tuples(quantities, prices), min_size=1, max_size=8), st.data())
def test_property_sell_everything_realizes_exact_total(
    lots: list[tuple[Decimal, Decimal]], data: st.DataObject
) -> None:
    txs = [b.trade(c(i), TxType.BUY, THYAO, TRY, q, p) for i, (q, p) in enumerate(lots)]
    total_qty = sum(q for q, _ in lots)
    total_cost = sum(q * p for q, p in lots)
    cut = data.draw(st.integers(min_value=0, max_value=int(total_qty)).map(Decimal))
    day = len(lots) + 1
    if cut:
        txs.append(b.trade(c(day), TxType.SELL, THYAO, TRY, cut, Decimal("1")))
    if total_qty - cut:
        txs.append(b.trade(c(day + 1), TxType.SELL, THYAO, TRY, total_qty - cut, Decimal("1")))
    s = replay(txs)
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == 0
    assert pos.cost == 0
    # Average and FIFO both release exactly the total cost when everything is sold.
    assert sum(r.cost for r in s.realizations) == total_cost
    assert sum(r.fifo_cost for r in s.realizations) == total_cost


@given(quantities, prices, st.sampled_from(["2", "3", "0.5", "1.5"]))
def test_property_split_conserves_cost_and_lot_quantity(
    qty: Decimal, price: Decimal, ratio: str
) -> None:
    split = ca(CorporateActionType.SPLIT, 2, ratio=Decimal(ratio))
    received = qty * (Decimal(ratio) - 1)
    s = replay(
        [
            b.trade(c(1), TxType.BUY, THYAO, TRY, qty, price),
            b.share_count_action(c(2), split, TRY, received),
        ]
    )
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.cost == qty * price == pos.fifo_cost
    assert pos.quantity == qty * Decimal(ratio) == sum(lot.quantity for lot in pos.lots)


@given(st.lists(st.tuples(quantities, prices), min_size=1, max_size=5))
def test_property_reversed_trades_vanish(trades: list[tuple[Decimal, Decimal]]) -> None:
    base = _with_id(b.trade(c(0), TxType.BUY, THYAO, TRY, Decimal(7), Decimal(3)), 1)
    extra = [
        _with_id(b.trade(c(i + 1), TxType.BUY, THYAO, TRY, q, p), i + 2)
        for i, (q, p) in enumerate(trades)
    ]
    revs = [_with_id(b.reversal(c(50 + i), t.id or 0), 100 + i) for i, t in enumerate(extra)]
    s = replay([base, *extra, *revs])
    pos = s.position("A", THYAO)
    assert pos is not None
    assert pos.quantity == 7
    assert pos.cost == 21


# ----------------------------------------------------------------- review findings (2026-10-19)
def _usd(day: int, side: TxType, fee_try: str = "0", fx: str = "34.37") -> Transaction:
    return b.trade(
        c(day),
        side,
        AAPL,
        USD,
        Decimal("10"),
        Decimal("100"),
        fee=Decimal(fee_try),
        fee_currency=TRY,
        fx_rate_try=Decimal(fx),
    )


def test_try_fee_on_foreign_trade_is_exact_in_try() -> None:
    s = replay([_usd(1, TxType.BUY, "10"), _usd(2, TxType.SELL, "10")])
    r = s.realizations[0]
    assert r.cost_try == Decimal("34380")  # 1000 x 34.37 + 10, no divide-and-multiply residue
    assert r.proceeds_try == Decimal("34360")  # 1000 x 34.37 - 10
    assert r.realized_try == Decimal("-20")


def test_worthless_foreign_delisting_needs_no_fx_rate() -> None:
    d = ca(CorporateActionType.DELISTING, 3, inst=AAPL)
    s = replay([_usd(1, TxType.BUY, fx="35"), b.delisting(c(3), d, USD, Decimal("10"))])
    r = s.realizations[0]
    assert r.realized == Decimal("-1000")
    assert r.realized_try == Decimal("-35000")


def test_cross_currency_merger_is_rejected() -> None:
    xetra = InstrumentId.parse("NEWCO.XETRA")
    m = ca(CorporateActionType.MERGER, 3, inst=AAPL, ratio=Decimal("1"), new_instrument=xetra)
    eur = b.merger(c(3), m, USD, Decimal("10"), Currency("EUR"), Decimal("10"))
    with pytest.raises(LedgerError, match="cross-currency"):
        replay([_usd(1, TxType.BUY), eur])


def test_corporate_action_on_shares_in_transit_is_flagged() -> None:
    out = b.securities_transfer(c(2, "A"), TxType.TRANSFER_OUT, "t1", THYAO, TRY, Decimal("100"))
    split = b.share_count_action(
        c(3), ca(CorporateActionType.SPLIT, 3, ratio=Decimal("2")), TRY, Decimal("50")
    )
    s = replay([buy(1, "150", "10"), out, split])
    assert any("in transit" in w for w in s.warnings)


def test_negative_cash_is_flagged() -> None:
    s = replay([buy(1, "10", "10")])
    assert any("cash is negative" in w for w in s.warnings)
    funded = [b.cash_flow(c(0), TxType.DEPOSIT, TRY, Decimal("100")), buy(1, "10", "10")]
    assert not any("negative" in w for w in replay(funded).warnings)


# ---------------------------------------------------------------- replay_steps
def test_replay_steps_match_replay_at_each_date() -> None:
    txs = [
        b.cash_flow(c(0), TxType.DEPOSIT, TRY, Decimal(5000)),
        buy(1, "100", "10", "1"),
        buy(1, "50", "12"),
        sell(3, "30", "15", "1"),
        b.cash_flow(c(5), TxType.WITHDRAWAL, TRY, Decimal(100)),
    ]
    seen: list[date] = []
    for day, state in replay_steps(txs):
        seen.append(day)
        expected = replay(txs, as_of=day)
        assert state.open_positions() == expected.open_positions()
        assert state.cash_balances() == expected.cash_balances()
        assert state.realizations == expected.realizations
    assert seen == [date(2025, 1, 1) + timedelta(days=d) for d in (0, 1, 3, 5)]


def test_replay_steps_honour_as_of_known_at_and_reversals() -> None:
    first = buy(1, "10", "10")
    first = Transaction(**{**_fields(first), "id": 1})
    rev = b.reversal(c(2), 1)
    later = buy(4, "5", "10")
    days = [d for d, _ in replay_steps([first, rev, later], as_of=date(2025, 1, 4))]
    assert days == []  # the only transaction up to Jan 4 was reversed
    days = [d for d, _ in replay_steps([first, rev, later], known_at=T0 + timedelta(days=1))]
    assert days == [date(2025, 1, 2)]  # the reversal was not yet recorded


def test_in_transit_is_visible_until_received() -> None:
    txs = [
        buy(0, "10", "10"),
        b.securities_transfer(c(1), TxType.TRANSFER_OUT, "t1", THYAO, TRY, Decimal(4)),
        b.securities_transfer(c(3, "B"), TxType.TRANSFER_IN, "t1", THYAO, TRY, Decimal(4)),
    ]
    mid = replay(txs, as_of=date(2025, 1, 2))
    [item] = mid.in_transit()
    assert (item.transfer_id, item.from_account, item.instrument, item.quantity) == (
        "t1",
        "A",
        THYAO,
        Decimal(4),
    )
    assert replay(txs).in_transit() == []


def _fields(tx: Transaction) -> dict[str, object]:
    from dataclasses import fields

    return {f.name: getattr(tx, f.name) for f in fields(tx) if f.init}
