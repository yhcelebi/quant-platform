"""Weight models against closed forms and invariants (ADR 0016, 1-4)."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from qp.core import QPValidationError
from qp.optimization.methods import (
    Estimator,
    covariance,
    cvar,
    hrp,
    max_sharpe,
    min_cvar,
    min_variance,
    risk_contributions,
    risk_parity,
)

DIAG = np.diag([0.04, 0.01, 0.0025])  # vols 20%, 10%, 5%, uncorrelated


def test_min_variance_uncorrelated_is_inverse_variance() -> None:
    """Uncorrelated: w_i ∝ 1/var_i = 25, 100, 400 → 1/21, 4/21, 16/21."""
    w = min_variance(DIAG).weights
    assert w == pytest.approx([1 / 21, 4 / 21, 16 / 21], abs=1e-6)


def test_min_variance_respects_cap() -> None:
    """Cap 0.5: the lowest-variance asset is pinned at 0.5; the other two split the rest
    in inverse-variance proportion 25:100 → 0.1, 0.4."""
    w = min_variance(DIAG, cap=0.5).weights
    assert w == pytest.approx([0.1, 0.4, 0.5], abs=1e-6)
    with pytest.raises(QPValidationError):
        min_variance(DIAG, cap=0.3)


def test_max_sharpe_uncorrelated_closed_form() -> None:
    """Uncorrelated tangency: w_i ∝ mu_i/var_i. mu = 0.08, 0.04, 0.01 → 2, 4, 4 → 0.2, 0.4, 0.4."""
    r = max_sharpe(np.array([0.08, 0.04, 0.01]), DIAG)
    assert r.weights == pytest.approx([0.2, 0.4, 0.4], abs=1e-5)
    assert any("estimation error" in n for n in r.notes)
    bad = max_sharpe(np.array([-0.01, -0.02, -0.03]), DIAG)
    assert any("risk-free" in n for n in bad.notes)


def test_risk_parity_uncorrelated_is_inverse_vol_and_equal_contributions() -> None:
    """Uncorrelated ERC: w_i ∝ 1/sd_i = 5, 10, 20 → 1/7, 2/7, 4/7."""
    w = risk_parity(DIAG).weights
    assert w == pytest.approx([1 / 7, 2 / 7, 4 / 7], abs=1e-6)
    assert risk_contributions(w, DIAG) == pytest.approx([1 / 3] * 3, abs=1e-6)


def test_risk_parity_correlated_contributions_equal() -> None:
    cov = np.array([[0.04, 0.006, 0.0], [0.006, 0.01, 0.001], [0.0, 0.001, 0.0025]])
    w = risk_parity(cov).weights
    assert risk_contributions(w, cov) == pytest.approx([1 / 3] * 3, abs=1e-6)


def test_hrp_two_assets_is_inverse_variance_and_independent_of_correlation() -> None:
    """Two assets: one bisection with inverse-variance weights 1/0.04 : 1/0.01 = 0.2 : 0.8."""
    for rho in (-0.5, 0.0, 0.9):
        c = rho * 0.2 * 0.1
        w = hrp(np.array([[0.04, c], [c, 0.01]])).weights
        assert w == pytest.approx([0.2, 0.8], abs=1e-12)


def test_hrp_groups_correlated_assets() -> None:
    """A and B nearly identical, C independent with the same variance: the {A,B} cluster
    and C split by cluster variance (≈ 0.0099 vs 0.01), so C gets about half and A, B
    about a quarter each — not a third each."""
    cov = np.array([[0.01, 0.0098, 0.0], [0.0098, 0.01, 0.0], [0.0, 0.0, 0.01]])
    w = hrp(cov).weights
    assert w[2] == pytest.approx(0.4975, abs=1e-3)
    assert w[0] == pytest.approx(w[1])


def test_cvar_by_hand() -> None:
    """Losses 1..100 (x0.001): CVaR95 = mean of the five largest = 0.098."""
    rets = -np.arange(1, 101, dtype=float)[:, None] / 1000
    assert cvar(np.array([1.0]), rets, 0.95) == pytest.approx(0.098)
    assert cvar(np.array([1.0]), rets, 0.99) == pytest.approx(0.100)


def test_min_cvar_prefers_the_asset_without_tail_losses_and_matches_its_objective() -> None:
    rng = np.random.default_rng(7)
    calm = rng.normal(0, 0.005, 400)
    wild = rng.normal(0, 0.005, 400)
    wild[::40] -= 0.08  # ten crashes
    r = np.column_stack([wild, calm])
    w = min_cvar(r, level=0.95).weights
    assert w[1] > 0.9
    grid = [np.array([x, 1 - x]) for x in np.linspace(0, 1, 101)]
    best = min(cvar(g, r, 0.95) for g in grid)
    assert cvar(w, r, 0.95) <= best + 1e-9


def test_covariance_estimators() -> None:
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.01, (300, 4))
    sample = covariance(r, Estimator.SAMPLE)
    assert sample == pytest.approx(np.cov(r, rowvar=False), rel=1e-12)
    lw = covariance(r)
    assert lw.shape == (4, 4)
    assert np.allclose(lw, lw.T)
    # shrinkage pulls off-diagonals towards zero
    off = ~np.eye(4, dtype=bool)
    assert np.abs(lw[off]).sum() < np.abs(sample[off]).sum()
    with pytest.raises(QPValidationError):
        covariance(r[:1])


@settings(max_examples=40, deadline=None)
@given(
    st.integers(min_value=2, max_value=6),
    st.integers(min_value=0, max_value=10_000),
    st.sampled_from([0.4, 0.6, 1.0]),
)
def test_every_method_is_long_only_fully_invested_and_capped(n: int, seed: int, cap: float) -> None:
    if cap * n < 1:
        return
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 0.01, (200, n)) + rng.normal(0, 0.01, (200, 1))
    cov = covariance(a)
    for w in (
        min_variance(cov, cap=cap).weights,
        max_sharpe(a.mean(axis=0), cov, cap=cap).weights,
        min_cvar(a, cap=cap).weights,
        risk_parity(cov).weights,
        hrp(cov).weights,
    ):
        assert w.sum() == pytest.approx(1.0, abs=1e-9)
        assert (w >= -1e-12).all()
    for w in (
        min_variance(cov, cap=cap).weights,
        max_sharpe(a.mean(axis=0), cov, cap=cap).weights,
        min_cvar(a, cap=cap).weights,
    ):
        assert (w <= cap + 1e-9).all()
    mv = min_variance(cov, cap=cap).weights
    eq = np.full(n, 1 / n)
    assert float(mv @ cov @ mv) <= float(eq @ cov @ eq) + 1e-12
