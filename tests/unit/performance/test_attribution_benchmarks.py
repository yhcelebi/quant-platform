"""P&L attribution and benchmarks, checked against hand calculations (ADR 0010, 11-13)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from qp.core import TRY, USD, Currency, InstrumentId
from qp.data.manual import ManualMarketData, default_known_time
from qp.data.market import FxPoint, PricePoint
from qp.data.series import ManualSeries, SeriesPoint
from qp.ledger import builders as b
from qp.ledger.domain import TxType
from qp.performance.attribution import OTHER, Attribution, attribute
from qp.performance.benchmarks import BenchmarkKind, BenchmarkResult, BenchmarkSpec, benchmark
from qp.performance.metrics import Unavailable, View

THY = InstrumentId.parse("THYAO.BIST")
AAPL = InstrumentId.parse("AAPL.NASDAQ")
LATEST = datetime.max.replace(tzinfo=UTC)
F3, M6, T7, W8, R9, F10 = (date(2025, 1, d) for d in (3, 6, 7, 8, 9, 10))


def c(d: date) -> b.Common:
    return b.Common("A", d, datetime(2025, 1, d.day, 20, tzinfo=UTC))


def price(inst: InstrumentId, d: date, v: str, cur: Currency = TRY) -> PricePoint:
    return PricePoint(inst, d, Decimal(v), cur, "t", default_known_time(d))


def usd(d: date, v: str) -> FxPoint:
    return FxPoint(USD, d, Decimal(v), "t", default_known_time(d))


TXS = [
    b.cash_flow(c(F3), TxType.DEPOSIT, TRY, Decimal(10000)),
    b.trade(c(F3), TxType.BUY, THY, TRY, Decimal(100), Decimal(50)),
    b.fx_conversion(c(F3), TRY, Decimal(3500), USD, Decimal(100)),
    b.cash_flow(c(T7), TxType.FEE, TRY, Decimal(10)),
    b.trade(c(W8), TxType.BUY, AAPL, USD, Decimal("0.4"), Decimal(200), fx_rate_try=Decimal(36)),
    b.trade(c(R9), TxType.SELL, THY, TRY, Decimal(50), Decimal(55), fee=Decimal(5)),
    b.cash_flow(c(R9), TxType.DEPOSIT, TRY, Decimal(1000)),
]
MARKET = ManualMarketData(
    [
        price(THY, F3, "50"),
        price(THY, W8, "55"),
        price(THY, F10, "60"),
        price(AAPL, W8, "200", USD),
        price(AAPL, F10, "210", USD),
    ],
    [usd(F3, "35"), usd(W8, "36"), usd(F10, "37")],
)


def test_attribution_by_hand() -> None:
    """Start (Mon 6 Jan): THYAO 100 x 50 = 5000, cash 1500 TRY, 100 USD x 35 = 3500;
    V = 10000. Moves: fee 10 TRY; buy 0.4 AAPL @ 200 USD at 36 (80 USD = 2880 TRY); sell 50
    THYAO @ 55 less 5 fee (+2745 TRY); deposit 1000 TRY.
    End (Fri 10 Jan): THYAO 50 x 60 = 3000; AAPL 84 USD x 37 = 3108; cash TRY
    1500 - 10 + 2745 + 1000 = 5235; 20 USD x 37 = 740. V = 12083; P&L = 12083 - 10000 -
    1000 = 1083 =
      THYAO   3000 - 5000 + 2745 =  745  (50 sold 50 -> 54.9 net: 245; 50 kept 50 -> 60: 500)
      AAPL    3108 - 2880        =  228  (price (84 - 80) x 37 = 148; FX 80 x (37 - 36) = 80)
      USD     740 - 3500 + 2880  =  120  (80 USD 35 -> 36: 80; 20 USD 35 -> 37: 40)
      TRY     5235 - 1500 - 3735 =    0
      fees                       =  -10
    """
    result = attribute(TXS, MARKET, start=M6, end=F10)
    assert isinstance(result, Attribution), result
    assert (result.start_value, result.end_value, result.external_flows) == (
        Decimal(10000),
        Decimal(12083),
        Decimal(1000),
    )
    assert result.total_pnl == Decimal(1083)
    assert result.residual == 0
    rows = {r.bucket: r for r in result.rows}
    assert rows[("instrument", "THYAO.BIST")].pnl == Decimal(745)
    assert rows[("instrument", "THYAO.BIST")].fx_effect == 0
    aapl = rows[("instrument", "AAPL.NASDAQ")]
    assert (aapl.pnl, aapl.pnl_local, aapl.price_effect, aapl.fx_effect) == (
        Decimal(228),
        Decimal(4),
        Decimal(148),
        Decimal(80),
    )
    assert rows[("cash", "USD")].pnl == Decimal(120)
    assert ("cash", "TRY") not in rows or rows[("cash", "TRY")].pnl == 0
    assert rows[OTHER].pnl == Decimal(-10)
    assert [r.label for r in result.rows][:2] == ["AAPL.NASDAQ", "THYAO.BIST"]


def test_attribution_needs_prices_at_both_ends() -> None:
    result = attribute(TXS, ManualMarketData([], [usd(F3, "35")]), start=M6, end=F10)
    assert isinstance(result, Unavailable)
    assert "THYAO.BIST: no price" in result.reason
    assert isinstance(attribute(TXS, MARKET, start=F10, end=M6), Unavailable)


def test_benchmarks_fx_price_and_rate() -> None:
    days = [M6, T7, W8, R9, F10]
    series = ManualSeries(
        [
            SeriesPoint(
                "DEP", date(2024, 12, 1), Decimal("36.5"), "t", datetime(2025, 1, 1, tzinfo=UTC)
            )
        ]
    )
    fx_spec = BenchmarkSpec("USD/TRY", BenchmarkKind.FX, currency="USD")
    r = benchmark(fx_spec, days, View.TRY, MARKET, series, as_of=LATEST)
    assert isinstance(r, BenchmarkResult)
    assert r.total == Decimal(37) / Decimal(35) - 1
    usd_view = benchmark(
        fx_spec, days, View.USD, MARKET, series, as_of=LATEST, usd_try=lambda d: _rate(d)
    )
    assert isinstance(usd_view, BenchmarkResult)
    assert usd_view.total == 0

    aapl = BenchmarkSpec("Apple", BenchmarkKind.PRICE, instrument="AAPL.NASDAQ")
    assert isinstance(benchmark(aapl, days, View.TRY, MARKET, series, as_of=LATEST), Unavailable)
    r = benchmark(aapl, [W8, R9, F10], View.TRY, MARKET, series, as_of=LATEST)
    assert isinstance(r, BenchmarkResult)
    assert r.total == Decimal(210 * 37) / Decimal(200 * 36) - 1

    # 36.5% simple annual, scale 0.01: 0.1% a day; Fri -> Mon counts 3 days
    dep = BenchmarkSpec("deposit", BenchmarkKind.RATE, series="DEP", scale=Decimal("0.01"))
    r = benchmark(dep, [F3, M6, T7], View.TRY, MARKET, series, as_of=LATEST)
    assert isinstance(r, BenchmarkResult)
    assert r.total == Decimal("1.003") * Decimal("1.001") - 1
    with pytest.raises(Exception, match="needs its source field"):
        BenchmarkSpec("bad", BenchmarkKind.RATE)


def _rate(d: date) -> Decimal | None:
    p = MARKET.fx_try(USD, d, as_of=LATEST)
    return p.rate_try if p else None


def test_attribution_sums_on_case_with_weekend_and_no_moves() -> None:
    result = attribute(TXS[:3], MARKET, start=M6, end=M6 + timedelta(days=4))
    assert isinstance(result, Attribution)
    assert result.residual == 0
    assert result.total_pnl == Decimal(1000 + 200)  # 100 THYAO 50 -> 60; 100 USD 35 -> 37
