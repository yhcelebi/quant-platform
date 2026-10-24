"""Daily history and performance metrics, with hand calculations (ADR 0010)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qp.core import TRY, USD, InstrumentId
from qp.data.manual import ManualMarketData, default_known_time
from qp.data.market import FxPoint, PricePoint
from qp.data.series import SeriesPoint
from qp.ledger import builders as b
from qp.ledger.domain import Transaction, TxType
from qp.performance.history import build_history, valuation_days
from qp.performance.metrics import (
    InflationIndex,
    Summary,
    Unavailable,
    View,
    annualize,
    performance,
    period_returns,
)

THY = InstrumentId.parse("THYAO.BIST")
T0 = datetime(2025, 1, 1, 20, tzinfo=UTC)


def c(d: date, account: str = "A") -> b.Common:
    return b.Common(account, d, T0 + timedelta(days=(d - date(2025, 1, 1)).days))


def px(d: date, value: str) -> PricePoint:
    return PricePoint(THY, d, Decimal(value), TRY, "test", default_known_time(d))


def usd(d: date, value: str) -> FxPoint:
    return FxPoint(USD, d, Decimal(value), "test", default_known_time(d))


def deposit(d: date, amount: str, account: str = "A") -> Transaction:
    return b.cash_flow(c(d, account), TxType.DEPOSIT, TRY, Decimal(amount))


def buy(d: date, qty: str, price: str, account: str = "A") -> Transaction:
    return b.trade(c(d, account), TxType.BUY, THY, TRY, Decimal(qty), Decimal(price))


def ok(result: Summary | Unavailable) -> Summary:
    assert isinstance(result, Summary), result
    return result


# ------------------------------------------------------------------- reference case e
D1, D2, D3 = date(2025, 1, 1), date(2025, 7, 2), date(2026, 1, 1)
CASE_E_TXS = [deposit(D1, "1000"), buy(D1, "100", "10"), deposit(D2, "1000")]
CASE_E_MARKET = ManualMarketData(
    [px(D1, "10"), px(D2, "11"), px(D3, "8.9")],
    [usd(D1, "30"), usd(D2, "33"), usd(D3, "40")],
)


def test_case_e_through_the_ledger() -> None:
    """Reference case e built from transactions: 1000 deposited and invested (100 x 10);
    on 2 Jul the shares are worth 1100 and 1000 more is deposited (V = 2100, F = 1000);
    on 1 Jan 2026 the shares are worth 890 (V = 1890).
    Daily returns are zero except 2 Jul: (2100 - 1000) / 1000 - 1 = 0.10 and
    1 Jan: 1890 / 2100 - 1 = -0.10. TWR = 1.1 x 0.9 - 1 = -0.01; XIRR -0.0728083."""
    h = build_history(CASE_E_TXS, CASE_E_MARKET, end=D3)
    s = ok(performance(h))
    assert (s.start, s.end, s.start_value, s.end_value) == (D1, D3, Decimal(1000), Decimal(1890))
    assert s.net_flows == Decimal(1000)
    assert s.pnl == Decimal(-110)
    assert s.twr == Decimal("-0.01")
    assert s.xirr == pytest.approx(-0.0728083, abs=5e-7)
    assert [r for _, r in s.returns if r != 0] == [Decimal("0.1"), Decimal("-0.1")]
    assert s.twr_annualized is not None  # 365 days: annualized equals the total
    assert s.twr_annualized == pytest.approx(Decimal("-0.01"), abs=Decimal("1e-25"))
    assert s.max_drawdown is not None
    # peak = the last day at the high-water mark (wealth stayed at 1.1 from 2 Jul)
    assert (s.max_drawdown.peak, s.max_drawdown.trough) == (date(2025, 12, 31), D3)
    assert s.max_drawdown.depth == Decimal("-0.1")
    assert s.max_drawdown.recovery is None
    assert s.current_drawdown == Decimal("-0.1")
    assert s.stale  # carried-forward prices are flagged, not hidden


def test_usd_view_is_try_twr_adjusted_by_usdtry() -> None:
    """USD TWR = (1 + TWR_TRY) x U_start / U_end - 1 = 0.99 x 30 / 40 - 1 = -0.2575."""
    h = build_history(CASE_E_TXS, CASE_E_MARKET, end=D3)
    s = ok(performance(h, View.USD, usd_try=lambda d: _usd_on(d)))
    assert s.twr == pytest.approx(Decimal("-0.2575"), abs=Decimal("1e-24"))
    assert s.start_value == Decimal(1000) / Decimal(30)


def _usd_on(d: date) -> Decimal | None:
    point = CASE_E_MARKET.fx_try(USD, d, as_of=datetime.max.replace(tzinfo=UTC))
    return point.rate_try if point else None


def test_real_view_is_fisher_on_interpolated_index() -> None:
    """Index Dec 2024 = 100, Dec 2025 = 130, Jan 2026 = 133 (and every month between
    known), so I(1 Jan 2025) = 100 x (L_jan / 100) ** (1/31) and I(1 Jan 2026) =
    130 x (133 / 130) ** (1/31). Real TWR = (1 + TWR) x I(s) / I(e) - 1."""
    levels = {(2024, 12): "100"} | {(2025, m): str(100 + 2.5 * m) for m in range(1, 13)}
    levels |= {(2026, 1): "133"}
    known = datetime(2026, 2, 5, tzinfo=UTC)
    index = InflationIndex(
        [SeriesPoint("TUFE", date(y, m, 1), Decimal(v), "t", known) for (y, m), v in levels.items()]
    )
    i_s = Decimal(100) * (Decimal("102.5") / Decimal(100)) ** (Decimal(1) / Decimal(31))
    i_e = Decimal(130) * (Decimal(133) / Decimal(130)) ** (Decimal(1) / Decimal(31))
    assert index.level(D1) == pytest.approx(i_s, abs=Decimal("1e-25"))
    assert index.level(date(2025, 1, 31)) == Decimal("102.5")
    h = build_history(CASE_E_TXS, CASE_E_MARKET, end=D3)
    s = ok(performance(h, View.REAL, index=index))
    expected = Decimal("0.99") * i_s / i_e - 1
    assert s.twr == pytest.approx(expected, abs=Decimal("1e-24"))
    assert s.end_value == Decimal(1890)  # expressed in TRY of the last day
    short = InflationIndex([SeriesPoint("TUFE", date(2024, 12, 1), Decimal(100), "t", known)])
    result = performance(h, View.REAL, index=short)
    assert isinstance(result, Unavailable)
    assert "inflation index not available" in result.reason


# ------------------------------------------------------------------- scope and transfers
MON = date(2025, 1, 6)
DAYS = [MON + timedelta(days=i) for i in range(5)]  # Mon..Fri
SCOPE_TXS = [
    deposit(DAYS[0], "1000"),
    buy(DAYS[1], "10", "50"),
    b.securities_transfer(c(DAYS[2]), TxType.TRANSFER_OUT, "t1", THY, TRY, Decimal(5)),
    b.securities_transfer(c(DAYS[4], "B"), TxType.TRANSFER_IN, "t1", THY, TRY, Decimal(5)),
]
SCOPE_MARKET = ManualMarketData([px(DAYS[0], "50"), px(DAYS[2], "60"), px(DAYS[4], "70")], [])


def test_transfer_out_of_scope_is_a_flow_on_receipt() -> None:
    """Scope {A}: Mon V=1000 (deposit); Tue buy 10 @ 50, V=1000; Wed price 60, 5 shares in
    transit still count for A: V = 500 + 10 x 60 = 1100 (r = 0.10); Fri price 70, the 5
    shares arrive in B: V = 500 + 5 x 70 = 850, F = -350, r = (850 + 350) / 1100 - 1.
    TWR = 1.1 x 1200 / 1100 - 1 = 0.2 (shares 50 -> 70 on half the capital)."""
    h = build_history(SCOPE_TXS, SCOPE_MARKET, end=DAYS[4], accounts={"A"})
    assert [(d.value, d.flow) for d in h.days] == [
        (Decimal(1000), Decimal(1000)),
        (Decimal(1000), Decimal(0)),
        (Decimal(1100), Decimal(0)),
        (Decimal(1100), Decimal(0)),
        (Decimal(850), Decimal(-350)),
    ]
    assert ok(performance(h)).twr == pytest.approx(Decimal("0.2"), abs=Decimal("1e-26"))
    both = build_history(SCOPE_TXS, SCOPE_MARKET, end=DAYS[4])
    assert [d.flow for d in both.days] == [Decimal(1000)] + [Decimal(0)] * 4
    assert ok(performance(both)).twr == Decimal("0.2")
    only_b = build_history(SCOPE_TXS, SCOPE_MARKET, end=DAYS[4], accounts={"B"})
    assert [(d.day, d.value, d.flow) for d in only_b.days] == [
        (DAYS[4], Decimal(350), Decimal(350))
    ]


def test_opening_balance_is_a_flow_at_market_value() -> None:
    """Opening 10 shares with cost 400 when the price is 45: F = V = 450, not the cost."""
    fri = date(2025, 1, 3)
    txs = [b.opening_position(c(fri), THY, TRY, Decimal(10), Decimal(400))]
    h = build_history(txs, ManualMarketData([px(fri, "45")], []), end=MON)
    assert [(d.day, d.value, d.flow) for d in h.days] == [
        (fri, Decimal(450), Decimal(450)),
        (MON, Decimal(450), Decimal(0)),
    ]


def test_weekend_flow_gets_its_own_valuation_day() -> None:
    sat = date(2025, 1, 4)
    h = build_history([deposit(sat, "100")], ManualMarketData([], []), end=MON)
    assert [(d.day, d.flow) for d in h.days] == [(sat, Decimal(100)), (MON, Decimal(0))]
    assert valuation_days(date(2025, 1, 3), MON) == [date(2025, 1, 3), MON]


def test_missing_price_makes_the_window_unavailable() -> None:
    txs = [deposit(DAYS[0], "1000"), buy(DAYS[1], "10", "50")]
    h = build_history(txs, ManualMarketData([px(DAYS[3], "55")], []), end=DAYS[4])
    assert h.days[1].value is None
    assert "THYAO.BIST: no price" in h.days[1].missing[0]
    result = performance(h)
    assert isinstance(result, Unavailable)
    assert result.reason.startswith(f"{DAYS[1]}: THYAO.BIST: no price")
    later = ok(performance(h, start=DAYS[3]))  # from the first priced day it works
    assert later.twr == Decimal(0)


def test_known_at_hides_later_recorded_transactions() -> None:
    late = buy(DAYS[1], "10", "50")
    late = b.trade(
        b.Common("A", DAYS[1], datetime(2025, 3, 1, tzinfo=UTC)),
        TxType.BUY,
        THY,
        TRY,
        Decimal(10),
        Decimal(50),
    )
    txs = [deposit(DAYS[0], "1000"), late]
    h = build_history(txs, SCOPE_MARKET, end=DAYS[4], known_at=datetime(2025, 2, 1, tzinfo=UTC))
    assert h.days[-1].value == Decimal(1000)  # the buy was not yet recorded: cash only


# ------------------------------------------------------------------- pure helpers
@given(st.lists(st.integers(1, 400), min_size=2, max_size=15))
def test_twr_without_flows_is_the_simple_return(prices: list[int]) -> None:
    days = valuation_days(MON, MON + timedelta(days=40))[: len(prices)]
    market = ManualMarketData([px(d, str(p)) for d, p in zip(days, prices, strict=False)], [])
    txs = [b.opening_position(c(days[0]), THY, TRY, Decimal(3), Decimal(1))]
    s = ok(performance(build_history(txs, market, end=days[-1])))
    assert s.twr == pytest.approx(
        Decimal(prices[-1]) / Decimal(prices[0]) - 1, abs=Decimal("1e-24")
    )


def test_annualize_and_period_returns() -> None:
    assert annualize(Decimal("0.21"), 730) == pytest.approx(Decimal("0.1"), abs=Decimal("1e-30"))
    assert annualize(Decimal("0.05"), 200) is None
    rets = [
        (date(2025, 1, 30), Decimal("0.1")),
        (date(2025, 1, 31), Decimal("0.1")),
        (date(2025, 2, 3), Decimal("-0.5")),
    ]
    assert period_returns(rets) == {(2025, 1): Decimal("0.21"), (2025, 2): Decimal("-0.5")}
    assert period_returns(rets, by="year") == {(2025, 0): Decimal("-0.395")}
