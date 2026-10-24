"""Hand-calculated checks for qp.stats (ADR 0010 conventions)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qp.core import QPValidationError
from qp.stats import (
    Drawdown,
    correlation,
    drawdown_series,
    drawdowns,
    historical_es,
    historical_var,
    parametric_es,
    parametric_var,
    volatility,
)

LOSSES = [float(x) for x in range(1, 101)]  # 1, 2, ..., 100


def test_historical_var_is_the_ceil_rank_loss() -> None:
    # n = 100: VaR95 = 95th smallest = 95; VaR99 = 99th smallest = 99.
    assert historical_var(LOSSES, 0.95) == 95.0
    assert historical_var(LOSSES, 0.99) == 99.0
    # n = 250, level 0.99: ceil(247.5) = 248th smallest = 3rd largest.
    assert historical_var([float(x) for x in range(250)], 0.99) == 247.0


def test_historical_es_is_mean_of_tail() -> None:
    assert historical_es(LOSSES, 0.95) == pytest.approx((95 + 96 + 97 + 98 + 99 + 100) / 6)
    assert historical_es(LOSSES, 0.99) == pytest.approx(99.5)


def test_parametric_matches_closed_form() -> None:
    r = [0.01, -0.01, 0.02, -0.02]  # mean 0, sample std sqrt(0.001/3)
    sigma = math.sqrt(0.001 / 3)
    z = 1.6448536269514722  # norm.ppf(0.95)
    assert parametric_var(r, 0.95) == pytest.approx(z * sigma, rel=1e-12)
    phi = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    assert parametric_es(r, 0.95) == pytest.approx(sigma * phi / 0.05, rel=1e-12)


def test_volatility_annualizes_sample_std() -> None:
    r = [0.01, -0.01, 0.01, -0.01]  # sample variance 4e-4 / 3
    assert volatility(r) == pytest.approx(math.sqrt(4e-4 / 3) * math.sqrt(252))
    assert volatility(r, periods_per_year=365) == pytest.approx(math.sqrt(4e-4 / 3 * 365))


def test_drawdowns_peak_trough_recovery() -> None:
    w = [1.0, 1.2, 0.9, 1.0, 1.25, 1.0, 1.1]
    assert drawdown_series(w) == pytest.approx([0, 0, -0.25, -1 / 6, 0, -0.2, -0.12])
    assert drawdowns(w) == [
        Drawdown(pytest.approx(-0.25), 1, 2, 4),  # type: ignore[arg-type]
        Drawdown(pytest.approx(-0.2), 4, 5, None),  # type: ignore[arg-type]
    ]


@given(st.lists(st.floats(0.5, 2.0), min_size=1, max_size=30))
def test_drawdown_series_is_bounded(growth: list[float]) -> None:
    wealth = [math.prod(growth[: i + 1]) for i in range(len(growth))]
    dd = drawdown_series(wealth)
    assert all(-1 < d <= 0 for d in dd)
    assert dd[0] == 0


def test_correlation_and_validation() -> None:
    assert correlation([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert correlation([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert math.isnan(correlation([1, 1, 1], [1, 2, 3]))
    with pytest.raises(QPValidationError):
        historical_var([], 0.95)
    with pytest.raises(QPValidationError):
        historical_var([1.0], 0.3)
    with pytest.raises(QPValidationError):
        volatility([1.0, math.nan])
