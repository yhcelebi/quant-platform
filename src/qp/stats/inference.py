"""Regression, stationarity, cointegration, GARCH and the bootstrap (ADR 0013, 1-4).

Thin, typed wrappers over statsmodels 0.15 and arch 8.0 so every study uses the same
settings. float64 in, plain dataclasses out.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import statsmodels.api as sm
from arch import arch_model
from statsmodels.tools.sm_exceptions import InterpolationWarning
from statsmodels.tsa.stattools import adfuller, coint, kpss

from qp.core import QPValidationError


def _array(values: Sequence[float], name: str, minimum: int) -> np.ndarray:
    a = np.asarray(values, dtype=np.float64)
    if a.ndim != 1 or a.size < minimum or not np.all(np.isfinite(a)):
        raise QPValidationError(f"{name} needs at least {minimum} finite values")
    return a


def newey_west_lags(n: int) -> int:
    """floor(4 (n / 100) ** (2/9))."""
    return math.floor(4 * float((n / 100) ** (2 / 9)))


@dataclass(frozen=True, slots=True)
class Regression:
    names: list[str]
    coef: list[float]
    stderr: list[float]  # HAC (Newey-West)
    tstat: list[float]
    pvalue: list[float]
    r2: float
    nobs: int
    lags: int

    def get(self, name: str) -> tuple[float, float]:
        """(coefficient, t-statistic) of a regressor."""
        i = self.names.index(name)
        return self.coef[i], self.tstat[i]


def ols(
    y: Sequence[float], columns: dict[str, Sequence[float]], *, constant: bool = True
) -> Regression:
    """OLS of y on the named columns, HAC standard errors (ADR 0013, 1)."""
    yy = _array(y, "y", 3)
    names = list(columns)
    cols = [_array(columns[n], n, 3) for n in names]
    if any(c.size != yy.size for c in cols):
        raise QPValidationError("regressors must have the same length as y")
    x = np.column_stack(cols) if cols else np.empty((yy.size, 0))
    if constant:
        x = sm.add_constant(x, has_constant="add")
        names = ["const", *names]
    lags = newey_west_lags(yy.size)
    fit = sm.OLS(yy, x).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return Regression(
        names,
        [float(v) for v in fit.params],
        [float(v) for v in fit.bse],
        [float(v) for v in fit.tvalues],
        [float(v) for v in fit.pvalues],
        float(fit.rsquared),
        int(fit.nobs),
        lags,
    )


@dataclass(frozen=True, slots=True)
class StationarityTest:
    test: str
    statistic: float
    pvalue: float
    lags: int
    pvalue_is_bound: bool  # KPSS p-values outside its table are clipped to 0.01 / 0.1


@dataclass(frozen=True, slots=True)
class Stationarity:
    adf: StationarityTest  # null: unit root
    kpss: StationarityTest  # null: stationary

    def verdict(self, alpha: float = 0.05) -> str:
        unit_root_rejected = self.adf.pvalue < alpha
        stationary_rejected = self.kpss.pvalue < alpha
        if unit_root_rejected and not stationary_rejected:
            return "stationary"
        if stationary_rejected and not unit_root_rejected:
            return "unit root"
        return "inconclusive"


def stationarity(x: Sequence[float]) -> Stationarity:
    a = _array(x, "stationarity", 20)
    adf = adfuller(a, autolag="AIC", result_object=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", InterpolationWarning)
        k = kpss(a, regression="c", nlags="auto", result_object=True)
    bound = any(issubclass(w.category, InterpolationWarning) for w in caught)
    return Stationarity(
        StationarityTest("ADF", float(adf.statistic), float(adf.pvalue), int(adf.lags), False),
        StationarityTest("KPSS", float(k.statistic), float(k.pvalue), int(k.lags), bound),
    )


@dataclass(frozen=True, slots=True)
class Cointegration:
    statistic: float
    pvalue: float
    hedge_ratio: float  # OLS slope of y on x
    critical_values: list[float]  # 1%, 5%, 10%


def engle_granger(y: Sequence[float], x: Sequence[float]) -> Cointegration:
    a, b = _array(y, "y", 20), _array(x, "x", 20)
    if a.size != b.size:
        raise QPValidationError("cointegration needs series of equal length")
    result = coint(a, b, trend="c", autolag="aic")
    slope = float(np.polyfit(b, a, 1)[0])
    return Cointegration(
        float(result.coint_t),
        float(result.pvalue),
        slope,
        [float(v) for v in result.critical_values],
    )


@dataclass(frozen=True, slots=True)
class Garch:
    mu: float
    omega: float
    alpha: float
    beta: float
    next_volatility: float  # one period ahead, in the units of the input returns
    loglik: float

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta


def garch11(returns: Sequence[float]) -> Garch:
    """GARCH(1,1), normal errors, constant mean; fitted on returns x 100 (arch's preferred
    scale) and reported back in the input units."""
    r = _array(returns, "GARCH", 50) * 100
    res = arch_model(r, vol="GARCH", p=1, q=1, dist="normal", mean="Constant").fit(disp="off")
    p = res.params
    var = float(res.forecast(horizon=1, reindex=False).variance.to_numpy()[-1, 0])
    return Garch(
        float(p["mu"]) / 100,
        float(p["omega"]) / 100**2,
        float(p["alpha[1]"]),
        float(p["beta[1]"]),
        math.sqrt(var) / 100,
        float(res.loglikelihood),
    )


@dataclass(frozen=True, slots=True)
class BootstrapCI:
    estimate: float
    low: float
    high: float
    level: float
    reps: int
    block: float
    seed: int


def stationary_indices(n: int, block: float, rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano: start anywhere; each step continues the block with probability
    1 - 1/block, else jumps to a new random start (wrapping around)."""
    idx = np.empty(n, dtype=np.int64)
    idx[0] = rng.integers(n)
    jumps = rng.random(n) < 1 / block
    starts = rng.integers(n, size=n)
    for t in range(1, n):
        idx[t] = starts[t] if jumps[t] else (idx[t - 1] + 1) % n
    return idx


def bootstrap(
    data: Sequence[float],
    statistic: Callable[[np.ndarray], float],
    *,
    reps: int = 2000,
    level: float = 0.95,
    seed: int = 0,
    block: float | None = None,
) -> BootstrapCI:
    """Percentile confidence interval of ``statistic`` under the stationary bootstrap."""
    a = _array(data, "bootstrap", 10)
    b = block if block is not None else a.size ** (1 / 3)
    rng = np.random.default_rng(seed)
    draws = np.array([statistic(a[stationary_indices(a.size, b, rng)]) for _ in range(reps)])
    tail = (1 - level) / 2
    return BootstrapCI(
        float(statistic(a)),
        float(np.quantile(draws, tail)),
        float(np.quantile(draws, 1 - tail)),
        level,
        reps,
        b,
        seed,
    )
