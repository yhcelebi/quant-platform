"""Valuation and factor tools, by hand (ADR 0013, 11-12)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pytest

from qp.core import QPValidationError
from qp.factors.models import factor_regression, quantile_long_short
from qp.fundamentals.facts import Snapshot, Value
from qp.valuation.dcf import dcf, grow, multiples, reverse_dcf, wacc


def test_dcf_by_hand() -> None:
    """FCFF 100, 110 at 10%, g 2%: PV 100/1.1 + 110/1.21 = 181.818; TV = 110 x 1.02 / 0.08
    = 1402.5, PV 1402.5 / 1.21 = 1159.091; EV 1340.909; less net debt 340.909 = 1000; 10
    shares: 100 each."""
    r = dcf([100, 110], discount_rate=0.10, terminal_growth=0.02, net_debt=340.909090909, shares=10)
    assert r.pv_explicit == pytest.approx(181.818181818)
    assert r.pv_terminal == pytest.approx(1159.090909091)
    assert r.enterprise_value == pytest.approx(1340.909090909)
    assert r.per_share == pytest.approx(100.0)
    assert r.terminal_share == pytest.approx(1159.090909091 / 1340.909090909)
    with pytest.raises(QPValidationError, match="below the discount rate"):
        dcf([1], discount_rate=0.05, terminal_growth=0.05)


def test_wacc_by_hand() -> None:
    """E 600, D 400: ke = 4% + 1.2 x 5% = 10%; WACC = 0.6 x 10% + 0.4 x 6% x 0.75 = 7.8%."""
    assert wacc(
        equity_value=600,
        debt_value=400,
        rf=0.04,
        beta=1.2,
        erp=0.05,
        cost_of_debt=0.06,
        tax_rate=0.25,
    ) == pytest.approx(0.078)


def test_reverse_dcf_recovers_growth() -> None:
    ev = dcf(grow(100, 0.10, 5), discount_rate=0.09, terminal_growth=0.02).enterprise_value
    assert reverse_dcf(
        ev, base_fcff=100, years=5, discount_rate=0.09, terminal_growth=0.02
    ) == pytest.approx(0.10, abs=1e-9)


def v(x: int) -> Value:
    return Value(Decimal(x), date(2025, 12, 31), "test", "c")


def test_multiples_by_hand() -> None:
    """Price 50 x 100 shares = 5000; net debt 1100 - 100 = 1000 -> EV 6000; P/E 5000/250 = 20,
    P/B 5000/2500 = 2, EV/EBITDA 6000/(500+100) = 10, EV/S 6000/3000 = 2, FCF yield
    (350-150)/5000 = 4%."""
    s = Snapshot("0000000001", datetime(2026, 1, 1, tzinfo=UTC))
    s.shares, s.net_income, s.equity, s.revenue = v(100), v(250), v(2500), v(3000)
    s.operating_income, s.d_and_a, s.cfo, s.capex = v(500), v(100), v(350), v(150)
    s.cash, s.debt_long = v(100), v(1100)
    m = multiples(s, 50.0)
    assert (m.market_cap, m.enterprise_value) == (5000.0, 6000.0)
    assert m.pe == pytest.approx(20)
    assert m.pb == pytest.approx(2)
    assert m.ev_ebitda == pytest.approx(10)
    assert m.ev_sales == pytest.approx(2)
    assert m.fcf_yield == pytest.approx(0.04)
    assert m.missing == []
    s.net_income = v(-10)
    assert multiples(s, 50.0).pe is None  # negative earnings: no P/E


def test_factor_regression_and_long_short() -> None:
    rng = np.random.default_rng(0)
    mkt = rng.normal(scale=0.01, size=500)
    y = 0.0002 + 1.3 * mkt + rng.normal(scale=0.001, size=500)
    r = factor_regression(list(y), {"mkt": list(mkt)})
    assert r.get("mkt")[0] == pytest.approx(1.3, abs=0.02)
    assert r.get("const")[0] == pytest.approx(0.0002, abs=0.0002)
    # 10 assets, characteristic = asset number; next return = number / 100:
    # top 2 (8, 9) average 0.085, bottom 2 (0, 1) average 0.005: factor 0.08
    d = date(2025, 1, 31)
    char = {d: {f"a{i}": float(i) for i in range(10)}}
    fwd = {d: {f"a{i}": i / 100 for i in range(10)}}
    [(day, ret)] = quantile_long_short(char, fwd, quantiles=5)
    assert day == d
    assert ret == pytest.approx(0.08)
    assert quantile_long_short(char, fwd, min_assets=20) == []
