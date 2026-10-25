"""Holdings through a weight model (ADR 0016, 1-5)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pytest

from qp.core import TRY, USD, InstrumentId
from qp.data.manual import ManualMarketData, default_known_time
from qp.data.market import FxPoint, PricePoint
from qp.optimization.methods import Method, covariance, min_variance
from qp.optimization.service import optimize_holdings, returns_matrix
from qp.performance.history import valuation_days
from qp.risk.var import Holding

LATEST = datetime.max.replace(tzinfo=UTC)
A = InstrumentId.parse("AAA.BIST")
B = InstrumentId.parse("BBB.BIST")
SPARSE = InstrumentId.parse("SPR.BIST")
DAYS = valuation_days(date(2025, 1, 6), date(2025, 12, 31))


def _market() -> ManualMarketData:
    """AAA and BBB follow seeded random walks; USD/TRY has no bulletin on Fridays; SPR has
    month-end prices only."""
    rng = np.random.default_rng(3)
    prices, fx = [], []
    a, b, rate = Decimal(100), Decimal(50), Decimal(40)
    for i, d in enumerate(DAYS):
        if i:
            a *= 1 + Decimal(str(round(rng.normal(0, 0.02), 6)))
            b *= 1 + Decimal(str(round(rng.normal(0, 0.01), 6)))
            rate *= 1 + Decimal(str(round(rng.normal(0.0005, 0.003), 6)))
        k = default_known_time(d)
        prices += [PricePoint(A, d, a, TRY, "t", k), PricePoint(B, d, b, TRY, "t", k)]
        if d.weekday() != 4:
            fx.append(FxPoint(USD, d, rate, "t", k))
        if i + 1 == len(DAYS) or DAYS[i + 1].month != d.month:
            prices.append(PricePoint(SPARSE, d, Decimal(10), TRY, "t", k))
    return ManualMarketData(prices, fx)


HOLDINGS = [
    Holding(A, TRY, Decimal(6000)),
    Holding(B, TRY, Decimal(3000)),
    Holding(None, USD, Decimal(1000)),
    Holding(None, TRY, Decimal(5000)),
    Holding(SPARSE, TRY, Decimal(500)),
]


def test_returns_matrix_uses_common_fresh_days_and_drops_sparse() -> None:
    md = _market()
    kept, r, used, excluded = returns_matrix(
        HOLDINGS[:3] + HOLDINGS[4:], DAYS, md, as_of=LATEST, min_observations=60
    )
    assert [h.name for h in kept] == ["AAA.BIST", "BBB.BIST", "cash USD"]
    assert excluded
    assert excluded[0].startswith("SPR.BIST")
    # Fridays drop out (no USD rate); Monday's USD return runs from Thursday and is fresh
    assert all(d.weekday() != 4 for d in used)
    assert r.shape == (len(used), 3)


def test_optimize_matches_the_method_and_trades_rebalance_the_invested_value() -> None:
    md = _market()
    res = optimize_holdings(
        HOLDINGS,
        DAYS,
        md,
        as_of=LATEST,
        day=DAYS[-1],
        method=Method.MIN_VARIANCE,
        lookback=500,
    )
    assert res.unavailable is None
    assert res.invested_try == Decimal(10000)  # TRY cash and SPR left out
    assert any("TRY cash" in n for n in res.notes)
    assert any("SPR.BIST" in e for e in res.excluded)
    _, r, _, _ = returns_matrix(HOLDINGS[:3], DAYS, md, as_of=LATEST, min_observations=60)
    expected = min_variance(covariance(r)).weights
    assert [a.target for a in res.assets] == pytest.approx(list(expected), abs=1e-9)
    assert [a.current for a in res.assets] == pytest.approx([0.6, 0.3, 0.1])
    trades = [a.trade_try for a in res.assets]
    assert all(t is not None for t in trades)
    assert abs(sum((t for t in trades if t is not None), Decimal(0))) <= Decimal("0.02")
    for a in res.assets:
        assert a.trade_try is not None
        assert float(a.value_try + a.trade_try) == pytest.approx(a.target * 10000, abs=0.01)
    assert res.volatility is not None
    assert res.current_volatility is not None
    assert res.volatility <= res.current_volatility
    assert sum(a.risk_share for a in res.assets) == pytest.approx(1.0)


@pytest.mark.parametrize("method", list(Method))
def test_every_method_runs_on_holdings(method: Method) -> None:
    res = optimize_holdings(
        HOLDINGS[:3], DAYS, _market(), as_of=LATEST, day=DAYS[-1], method=method
    )
    assert res.unavailable is None
    assert sum(a.target for a in res.assets) == pytest.approx(1.0)
    assert res.cvar is not None
    assert res.cvar > 0


def test_assets_not_held_get_weights_but_no_trades() -> None:
    unheld = [Holding(h.instrument, h.currency, Decimal(0)) for h in HOLDINGS[:3]]
    res = optimize_holdings(
        unheld, DAYS, _market(), as_of=LATEST, day=DAYS[-1], method=Method.MIN_VARIANCE
    )
    assert res.unavailable is None
    assert all(a.trade_try is None for a in res.assets)
    assert res.current_volatility is None
    assert any("none of these assets is held" in n for n in res.notes)


def test_unavailable_with_one_asset_or_short_history() -> None:
    md = _market()
    one = optimize_holdings(HOLDINGS[:1], DAYS, md, as_of=LATEST, day=DAYS[-1], method=Method.HRP)
    assert one.unavailable is not None
    assert "at least 2" in one.unavailable
    short = optimize_holdings(
        HOLDINGS[:2], DAYS[:30], md, as_of=LATEST, day=DAYS[29], method=Method.HRP
    )
    assert short.unavailable is not None
