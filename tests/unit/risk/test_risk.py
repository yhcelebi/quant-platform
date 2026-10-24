"""VaR/ES, exposure and correlation (ADR 0010, 14-16), checked by hand."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qp.core import TRY, USD, InstrumentId
from qp.data.manual import ManualMarketData, default_known_time
from qp.data.market import FxPoint, PricePoint
from qp.performance.history import valuation_days
from qp.risk.correlation import correlations
from qp.risk.exposure import AccountHolding, exposures
from qp.risk.var import Holding, Method, asset_returns, holdings_var, portfolio_var

LATEST = datetime.max.replace(tzinfo=UTC)
A = InstrumentId.parse("AAA.BIST")
B = InstrumentId.parse("BBB.BIST")
DAYS = valuation_days(date(2025, 1, 6), date(2025, 6, 30))  # 125 weekdays


def test_portfolio_var_by_hand() -> None:
    """Returns -0.001, -0.002, ..., -0.100 on a value of 1000: losses 1..100.
    Historical VaR95 = 95th smallest = 95, ES95 = mean(95..100) = 97.5; VaR99 = 99,
    ES99 = 99.5."""
    rets = [(DAYS[i], Decimal(-(i + 1)) / Decimal(1000)) for i in range(100)]
    r = portfolio_var(rets, Decimal(1000))
    hist95 = r.figure(Method.HISTORICAL, 0.95)
    hist99 = r.figure(Method.HISTORICAL, 0.99)
    assert hist95 is not None
    assert hist99 is not None
    assert (hist95.var, hist95.es) == (Decimal("95.00"), Decimal("97.50"))
    assert (hist99.var, hist99.es) == (Decimal("99.00"), Decimal("99.50"))
    par = r.figure(Method.PARAMETRIC, 0.95)
    assert par is not None
    mean = -0.0505
    sd = math.sqrt(sum((-(i + 1) / 1000 - mean) ** 2 for i in range(100)) / 99)
    assert float(par.var) == pytest.approx((1.6448536269514722 * sd - mean) * 1000, abs=0.006)
    short = portfolio_var(rets[:10], Decimal(1000))
    assert short.unavailable is not None
    assert short.figures == []


def _px(inst: InstrumentId, d: date, v: Decimal) -> PricePoint:
    return PricePoint(inst, d, v, TRY, "t", default_known_time(d))


def _fx(d: date, v: Decimal) -> FxPoint:
    return FxPoint(USD, d, v, "t", default_known_time(d))


def _market() -> ManualMarketData:
    """AAA moves +1%, -1% alternately every day; BBB = AAA's moves x 2; USD/TRY rises 0.1%
    a day except Fridays (no bulletin); SPARSE has month-end prices only."""
    prices, fx = [], []
    a = b = Decimal(100)
    rate = Decimal(40)
    sparse = InstrumentId.parse("SPR.BIST")
    for i, d in enumerate(DAYS):
        step = Decimal("0.01") if i % 2 == 0 else Decimal("-0.01")
        if i:
            a *= 1 + step
            b *= 1 + 2 * step
            if d.weekday() != 4:
                rate *= Decimal("1.001")
        prices += [_px(A, d, a), _px(B, d, b)]
        if d.weekday() != 4:
            fx.append(_fx(d, rate))
        if i + 1 == len(DAYS) or DAYS[i + 1].month != d.month:  # last weekday of a month
            prices.append(_px(sparse, d, Decimal(50)))
    return ManualMarketData(prices, fx)


def test_asset_returns_zero_without_fresh_data_and_move_lands_next() -> None:
    md = _market()
    rets, mask = asset_returns(Holding(None, USD, Decimal(1)), DAYS[:10], md, as_of=LATEST)
    # Jan 6 (Mon) first: no return; Fridays (Jan 10) have no rate: zero; the next Monday
    # carries two days of moves (Thu -> Mon: one 0.1% step, Friday had none).
    assert mask == [False, True, True, True, False, True, True, True, True, False]
    assert rets[4] == 0.0
    assert rets[5] == pytest.approx(0.001)


def test_holdings_var_scenarios_are_value_weighted_returns() -> None:
    md = _market()
    holdings = [
        Holding(A, TRY, Decimal(1000)),
        Holding(B, TRY, Decimal(500)),
        Holding(None, TRY, Decimal(300)),
        Holding(InstrumentId.parse("SPR.BIST"), TRY, Decimal(200)),
    ]
    r = holdings_var(holdings, DAYS, md, as_of=LATEST, lookback=100)
    assert r.unavailable is None
    assert r.value == Decimal(1800)  # SPARSE left out, named with its weight
    # window = last 101 days (from early Feb): month ends Feb..Jun = 5 points, 4 returns
    assert r.excluded == ["SPR.BIST (10.0% of value): 4 fresh returns"]
    # every scenario is +-(1000 x 1% + 500 x 2%) = +-20
    assert {abs(p) for _, p in r.scenarios} == {Decimal("20.00")}
    hist = r.figure(Method.HISTORICAL, 0.99)
    assert hist is not None
    assert hist.var == Decimal("20.00")
    assert holdings_var(holdings, DAYS[:30], md, as_of=LATEST).unavailable is not None


def test_correlations_use_shared_fresh_days() -> None:
    md = _market()
    sparse = InstrumentId.parse("SPR.BIST")
    hs = [
        Holding(A, TRY, Decimal(1)),
        Holding(B, TRY, Decimal(1)),
        Holding(sparse, TRY, Decimal(1)),
    ]
    m = correlations(hs, DAYS, md, as_of=LATEST)
    assert m.names == ["AAA.BIST", "BBB.BIST", "SPR.BIST"]
    assert m.values[0][1] == pytest.approx(1.0)
    assert m.common_days[0][1] == len(DAYS) - 1
    assert math.isnan(m.values[0][2])


def test_exposures_and_concentration() -> None:
    items = [
        AccountHolding("X", Holding(A, TRY, Decimal(600))),
        AccountHolding("Y", Holding(A, TRY, Decimal(200))),
        AccountHolding("Y", Holding(None, USD, Decimal(200))),
    ]
    r = exposures(items, lambda h: "cash" if h.instrument is None else "equity")
    assert [(e.name, e.weight) for e in r.by_holding] == [
        ("AAA.BIST", Decimal("0.8")),
        ("cash USD", Decimal("0.2")),
    ]
    assert [(e.name, e.weight) for e in r.by_currency] == [
        ("TRY", Decimal("0.8")),
        ("USD", Decimal("0.2")),
    ]
    assert [e.name for e in r.by_account] == ["X", "Y"]
    assert r.herfindahl == Decimal("0.68")  # 0.8^2 + 0.2^2
    assert r.effective_holdings == Decimal(1) / Decimal("0.68")
