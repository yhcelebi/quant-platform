"""Statistics for research (ADR 0013, 1-6): closed forms, hand cases and seeded simulations."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from qp.stats.inference import (
    bootstrap,
    engle_granger,
    garch11,
    newey_west_lags,
    ols,
    stationarity,
    stationary_indices,
)
from qp.stats.sharpe import (
    deflated_sharpe,
    expected_max_sharpe,
    pbo,
    probabilistic_sharpe,
    sharpe,
)

RNG = np.random.default_rng(42)


def test_ols_recovers_coefficients_with_hac_lags() -> None:
    x = RNG.normal(size=400)
    y = 2 + 3 * x + RNG.normal(scale=0.01, size=400)
    r = ols(list(y), {"x": list(x)})
    assert r.names == ["const", "x"]
    assert r.get("const")[0] == pytest.approx(2, abs=0.01)
    assert r.get("x")[0] == pytest.approx(3, abs=0.01)
    assert r.lags == newey_west_lags(400) == 5  # floor(4 * 4 ** (2/9)) = floor(5.43)
    assert r.r2 > 0.999


def test_stationarity_white_noise_vs_random_walk() -> None:
    """Over 20 seeds: white noise is called stationary and random walks unit roots in most
    draws (single draws can be inconclusive: both tests have size 5%)."""
    noise_calls, walk_calls = [], []
    for seed in range(20):
        e = np.random.default_rng(seed).normal(size=500)
        noise_calls.append(stationarity(list(e)).verdict())
        walk_calls.append(stationarity(list(np.cumsum(e))).verdict())
    assert noise_calls.count("stationary") >= 16
    assert walk_calls.count("unit root") >= 14
    assert "stationary" not in walk_calls


def test_engle_granger_finds_the_hedge_ratio() -> None:
    x = np.cumsum(RNG.normal(size=600))
    y = 2 * x + RNG.normal(size=600)
    c = engle_granger(list(y), list(x))
    assert c.pvalue < 0.01
    assert c.hedge_ratio == pytest.approx(2, abs=0.02)
    false_positives = sum(
        engle_granger(
            list(np.cumsum(np.random.default_rng(s).normal(size=600))),
            list(np.cumsum(np.random.default_rng(100 + s).normal(size=600))),
        ).pvalue
        < 0.05
        for s in range(20)
    )
    assert false_positives <= 4  # size 5%: about one in 20 independent pairs


def test_garch_recovers_simulated_parameters() -> None:
    """r_t = sigma_t z_t with sigma^2 = 1e-5 + 0.1 r^2 + 0.85 sigma^2 (seeded)."""
    rng = np.random.default_rng(7)
    n, omega, alpha, beta = 5000, 1e-5, 0.10, 0.85
    r = np.empty(n)
    var = omega / (1 - alpha - beta)
    for t in range(n):
        r[t] = math.sqrt(var) * rng.normal()
        var = omega + alpha * r[t] ** 2 + beta * var
    g = garch11(list(r))
    assert g.alpha == pytest.approx(alpha, abs=0.04)
    assert g.beta == pytest.approx(beta, abs=0.05)
    assert g.persistence < 1
    assert g.omega == pytest.approx(omega, rel=0.6)


def test_bootstrap_is_seeded_and_covers_the_mean() -> None:
    data = list(RNG.normal(loc=1.0, size=300))
    a = bootstrap(data, lambda x: float(np.mean(x)), reps=500, seed=3)
    b = bootstrap(data, lambda x: float(np.mean(x)), reps=500, seed=3)
    assert a == b
    assert a.low < 1.0 < a.high
    assert a.block == pytest.approx(300 ** (1 / 3))
    idx = stationary_indices(50, 5.0, np.random.default_rng(0))
    assert idx.min() >= 0
    assert idx.max() < 50


def test_sharpe_and_psr_closed_forms() -> None:
    r = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01]
    a = np.array(r)
    assert sharpe(r) == pytest.approx(a.mean() / a.std(ddof=1) * math.sqrt(252))
    sr = a.mean() / a.std(ddof=1)
    assert probabilistic_sharpe(r, sr) == pytest.approx(0.5)  # benchmark = estimate
    assert probabilistic_sharpe(r, 0.0) > 0.5


def test_expected_max_sharpe_by_hand() -> None:
    """N = 1000, V = 1: 0.42278 x Phi^-1(0.999) + 0.57722 x Phi^-1(1 - 1/(1000 e))
    = 0.42278 x 3.0902 + 0.57722 x 3.3765 = 3.2554."""
    assert expected_max_sharpe(1000, 1.0) == pytest.approx(3.2554, abs=1e-3)
    assert expected_max_sharpe(1000, 0.25) == pytest.approx(3.2554 / 2, abs=1e-3)
    r = list(RNG.normal(loc=0.001, scale=0.01, size=1000))
    assert deflated_sharpe(r, trials=100, trial_sr_variance=0.001) < probabilistic_sharpe(r)
    z = expected_max_sharpe(10, 1.0)
    assert z == pytest.approx(
        (1 - 0.5772156649015329) * norm.ppf(0.9)
        + 0.5772156649015329 * norm.ppf(1 - 1 / (10 * math.e))
    )


def test_pbo_hand_cases() -> None:
    # Two configs over 4 periods, 2 blocks: A wins the first half, B the second. Whichever
    # half is in-sample, its winner is the worse one out of sample: PBO = 1.
    m = np.array([[0.02, -0.01], [0.03, -0.02], [-0.02, 0.01], [-0.01, 0.03]])
    assert pbo(m, blocks=2).probability == 1.0
    # One config is consistently best: it wins in sample and out of sample: PBO = 0.
    rng = np.random.default_rng(1)
    noise = rng.normal(scale=0.01, size=(320, 10))
    noise[:, 3] += 0.01
    good = pbo(noise, blocks=8)
    assert good.probability == 0.0
    assert good.splits == math.comb(8, 4)
    # Pure noise: no skill, overfitting probability near one half.
    assert 0.2 < pbo(rng.normal(size=(320, 10)), blocks=8).probability < 0.8
