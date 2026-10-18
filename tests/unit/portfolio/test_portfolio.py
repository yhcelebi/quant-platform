from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from qp.core import TRY, USD, InstrumentId
from qp.data.manual import ManualDataError, ManualMarketData, default_known_time
from qp.data.market import FxPoint, PricePoint
from qp.ledger import builders as b
from qp.ledger.domain import TxType
from qp.ledger.engine import replay
from qp.portfolio.holdings import CostMethod, build_report
from qp.portfolio.reconcile import reconcile
from qp.portfolio.valuation import value_position

THYAO = InstrumentId.parse("THYAO.BIST")
AAPL = InstrumentId.parse("AAPL.NASDAQ")
T0 = datetime(2025, 1, 1, 12, tzinfo=UTC)


def c(day: int, account: str = "A") -> b.Common:
    return b.Common(account, date(2025, 1, 1) + timedelta(days=day), T0 + timedelta(days=day))


def price(
    inst: InstrumentId, day: date, value: str, cur: str = "TRY", known: datetime | None = None
) -> PricePoint:
    from qp.core import Currency

    return PricePoint(
        inst, day, Decimal(value), Currency(cur), "test", known or default_known_time(day)
    )


def fx(day: date, rate: str, known: datetime | None = None) -> FxPoint:
    return FxPoint(USD, day, Decimal(rate), "test", known or default_known_time(day))


# ---------------------------------------------------------------- manual market data
def test_manual_data_point_in_time() -> None:
    d1, d2 = date(2025, 1, 1), date(2025, 1, 2)
    early, late = datetime(2025, 1, 2, 16, tzinfo=UTC), datetime(2025, 1, 3, 9, tzinfo=UTC)
    md = ManualMarketData(
        [
            price(THYAO, d1, "10"),
            price(THYAO, d2, "11", known=early),
            price(THYAO, d2, "12", known=late),
        ],
        [fx(d1, "35")],
    )
    p = md.price(THYAO, d2, as_of=datetime(2025, 1, 4, tzinfo=UTC))
    assert p is not None
    assert p.price == Decimal("12")  # latest revision
    p = md.price(THYAO, d2, as_of=early)
    assert p is not None
    assert p.price == Decimal("11")  # revision not yet known
    p = md.price(THYAO, d2, as_of=datetime(2025, 1, 2, 10, tzinfo=UTC))
    assert p is not None
    assert p.valid_date == d1  # d2 not known yet at all
    assert md.price(THYAO, date(2024, 12, 31), as_of=late) is None
    f = md.fx_try(USD, d2, as_of=late)
    assert f is not None
    assert f.rate_try == Decimal("35")


def test_default_known_time_is_end_of_day_istanbul() -> None:
    assert default_known_time(date(2025, 1, 2)) == datetime(2025, 1, 2, 20, 59, 59, tzinfo=UTC)


def test_manual_data_files(tmp_path: Path) -> None:
    (tmp_path / "prices.csv").write_text(
        "valid_date,instrument,price,currency,source\n2025-01-02,THYAO.BIST,9.5,TRY,me\n"
    )
    (tmp_path / "fx.csv").write_text(
        "valid_date,currency,rate_try,source,known_time\n2025-01-02,USD,35.1,me,2025-01-02T15:30:00+03:00\n"
    )
    md = ManualMarketData.from_dir(tmp_path)
    f = md.fx_try(USD, date(2025, 1, 2), as_of=datetime(2025, 1, 2, 12, 31, tzinfo=UTC))
    assert f is not None
    assert f.rate_try == Decimal("35.1")
    assert (
        ManualMarketData.from_dir(tmp_path / "missing").price(THYAO, date(2025, 1, 2), as_of=T0)
        is None
    )


@pytest.mark.parametrize(
    "content",
    [
        "valid_date,instrument,price,currency,source\n2025-01-02,THYAO.BIST,-1,TRY,me\n",
        "valid_date,instrument,price,currency,source\n2025-01-02,THYAO.BIST,1.5,try,me\n",
        "valid_date,instrument,price,currency,source\n02.01.2025,THYAO.BIST,1,TRY,me\n",
        "date,instrument,price\n2025-01-02,THYAO.BIST,1\n",
        "valid_date,instrument,price,currency,source,known_time\n2025-01-02,THYAO.BIST,1,TRY,me,2025-01-02T10:00:00\n",
        "valid_date,instrument,price,currency,source\n2025-01-02,THYAO.BIST,12,50,TRY,me\n",
        "valid_date,instrument,price,currency,source\n2025-01-02,THYAO.BIST,12.50,TRY\n",
    ],
)
def test_manual_data_rejects_bad_rows(tmp_path: Path, content: str) -> None:
    (tmp_path / "prices.csv").write_text(content)
    with pytest.raises(ManualDataError):
        ManualMarketData.from_dir(tmp_path)


def test_manual_data_rejects_duplicates() -> None:
    with pytest.raises(ManualDataError, match="duplicate"):
        ManualMarketData([price(THYAO, date(2025, 1, 1), "1")] * 2, [])


# ---------------------------------------------------------------- valuation
def test_value_position_missing_inputs_are_none() -> None:
    v = value_position("A", AAPL, USD, Decimal(1), Decimal(100), Decimal(4000), None, Decimal(40))
    assert v.value is None
    assert v.value_try is None
    v = value_position("A", AAPL, USD, Decimal(1), Decimal(100), Decimal(4000), Decimal(110), None)
    assert v.value == Decimal(110)
    assert v.value_try is None
    v = value_position("A", THYAO, TRY, Decimal(1), Decimal(10), Decimal(10), Decimal(12), None)
    assert v.fx_effect_try == 0
    assert v.price_effect_try == Decimal(2)


# ---------------------------------------------------------------- holdings report
def _ledger() -> list:  # type: ignore[type-arg]
    return [
        b.cash_flow(c(0), TxType.DEPOSIT, TRY, Decimal("10000")),
        b.trade(c(1), TxType.BUY, THYAO, TRY, Decimal("100"), Decimal("10")),
        b.trade(c(2), TxType.BUY, THYAO, TRY, Decimal("100"), Decimal("20")),
        b.trade(c(3), TxType.SELL, THYAO, TRY, Decimal("50"), Decimal("30")),
        b.cash_flow(c(4), TxType.DEPOSIT, USD, Decimal("1000")),
        b.trade(
            c(5), TxType.BUY, AAPL, USD, Decimal("1"), Decimal("100"), fx_rate_try=Decimal("40")
        ),
    ]


def test_report_values_totals_and_methods() -> None:
    day = date(2025, 1, 10)
    md = ManualMarketData(
        [price(THYAO, day, "25"), price(AAPL, day, "110", "USD")], [fx(day, "42")]
    )
    avg = build_report(_ledger(), md, as_of=day)
    assert avg.complete
    by_inst = {r.valuation.instrument: r for r in avg.positions}
    assert by_inst[THYAO].valuation.cost == Decimal("2250")
    assert by_inst[AAPL].valuation.value_try == Decimal("4620")
    cash = {(r.account, r.currency.code): r for r in avg.cash}
    assert cash[("A", "TRY")].amount == Decimal("8500")
    assert cash[("A", "USD")].amount_try == Decimal(900) * 42
    expected_total = Decimal(150 * 25) + Decimal(4620) + Decimal(8500) + Decimal(900 * 42)
    assert avg.total_try == expected_total
    assert avg.total_usd == expected_total / 42
    assert [r.realized for r in avg.realized] == [Decimal("750")]

    fifo = build_report(_ledger(), md, as_of=day, method=CostMethod.FIFO)
    assert {r.valuation.instrument: r for r in fifo.positions}[THYAO].valuation.cost == Decimal(
        "2500"
    )


def test_report_flags_missing_and_stale_prices() -> None:
    md = ManualMarketData([price(THYAO, date(2025, 1, 1), "25")], [])
    report = build_report(_ledger(), md, as_of=date(2025, 1, 20))
    assert not report.complete
    text = "\n".join(report.warnings)
    assert "older than 7 days" in text
    assert "AAPL.NASDAQ: no price" in text
    assert "USD view unavailable" in text
    assert report.total_usd is None


def test_report_known_at_reproduces_old_view() -> None:
    day = date(2025, 1, 10)
    md = ManualMarketData(
        [price(THYAO, day, "25", known=T0), price(THYAO, day, "26", known=T0 + timedelta(days=30))],
        [fx(day, "42")],
    )
    then = build_report(_ledger(), md, as_of=day, known_at=T0 + timedelta(days=3, hours=1))
    rows = {r.valuation.instrument: r for r in then.positions}
    assert rows[THYAO].valuation.quantity == 150  # the sale on day 3 is recorded at T0 + 3d
    assert rows[THYAO].valuation.price == Decimal("25")
    assert AAPL not in rows  # bought later
    latest = {r.valuation.instrument: r for r in build_report(_ledger(), md, as_of=day).positions}
    assert latest[THYAO].valuation.price == Decimal("26")


def test_report_account_filter() -> None:
    md = ManualMarketData([], [])
    assert build_report(_ledger(), md, as_of=date(2025, 1, 10), accounts=["OTHER"]).positions == []


# ---------------------------------------------------------------- reconcile
def test_reconcile_reports_every_difference() -> None:
    state = replay(_ledger())
    head = "account,instrument,quantity,currency,amount\n"
    ok = head + "A,THYAO.BIST,150,,\nA,AAPL.NASDAQ,1,,\nA,,,TRY,8500\nA,,,USD,900\n"
    assert reconcile(state, ok) == []
    bad = head + "A,THYAO.BIST,149,,\nA,GARAN.BIST,5,,\nA,,,TRY,8500\n"
    diffs = {(m.item, m.difference) for m in reconcile(state, bad)}
    assert diffs == {
        ("THYAO.BIST", Decimal(1)),
        ("GARAN.BIST", Decimal(-5)),
        ("AAPL.NASDAQ", Decimal(1)),
        ("USD", Decimal(900)),
    }
    assert {m.item for m in reconcile(state, bad, include_cash=False)} == {
        "THYAO.BIST",
        "GARAN.BIST",
        "AAPL.NASDAQ",
    }


def test_report_flags_stale_fx_rates() -> None:
    md = ManualMarketData(
        [price(THYAO, date(2025, 6, 27), "25"), price(AAPL, date(2025, 6, 27), "200", "USD")],
        [fx(date(2025, 1, 2), "35")],
    )
    report = build_report(_ledger(), md, as_of=date(2025, 6, 30))
    stale = [w for w in report.warnings if "USD/TRY rate from 2025-01-02" in w]
    assert len(stale) == 1  # once per currency, not per position or cash row
    rows = {r.valuation.instrument: r for r in report.positions}
    assert rows[AAPL].stale
    assert not rows[THYAO].stale


def test_statement_with_wrong_field_count_is_rejected() -> None:
    from qp.core import QPError

    state = replay(_ledger())
    with pytest.raises(QPError, match="field count"):
        reconcile(state, "account,instrument,quantity\nA,THYAO.BIST,1,5\n")
