"""Portfolio weight models on a matrix of periodic returns (ADR 0016, 1-4).

Pure float64 (numpy/scipy/scikit-learn); long-only and fully invested. ``returns`` is T x N
(periods x assets), ``cov`` N x N in the same period units unless stated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import linprog, minimize
from scipy.spatial.distance import squareform
from sklearn.covariance import LedoitWolf

from qp.core import QPValidationError

type Matrix = NDArray[np.float64]
type Vector = NDArray[np.float64]

TOL = 1e-10


class Method(StrEnum):
    MIN_VARIANCE = "min_variance"
    MAX_SHARPE = "max_sharpe"
    RISK_PARITY = "risk_parity"
    HRP = "hrp"
    MIN_CVAR = "min_cvar"


class Estimator(StrEnum):
    LEDOIT_WOLF = "ledoit_wolf"
    SAMPLE = "sample"


@dataclass
class Weights:
    method: Method
    weights: Vector
    converged: bool = True
    notes: list[str] = field(default_factory=list)


def covariance(returns: Matrix, estimator: Estimator = Estimator.LEDOIT_WOLF) -> Matrix:
    """Covariance of the columns, per period."""
    r = np.asarray(returns, dtype=np.float64)
    if r.ndim != 2 or r.shape[0] < 2:
        raise QPValidationError("covariance needs a T x N matrix with T >= 2")
    if estimator is Estimator.SAMPLE:
        return np.atleast_2d(np.cov(r, rowvar=False, ddof=1))
    return np.asarray(LedoitWolf().fit(r).covariance_, dtype=np.float64)


def _check(cov: Matrix, cap: float) -> int:
    n = cov.shape[0]
    if cov.shape != (n, n) or n == 0:
        raise QPValidationError("covariance must be a non-empty square matrix")
    if cap * n < 1 - TOL:
        raise QPValidationError(f"cap {cap} is too low for {n} assets to be fully invested")
    return int(n)


def _slsqp(objective: object, n: int, cap: float, x0: Vector | None = None) -> tuple[Vector, bool]:
    start = np.full(n, 1.0 / n) if x0 is None else x0
    res = minimize(
        objective,  # type: ignore[call-overload]
        start,
        method="SLSQP",
        bounds=[(0.0, cap)] * n,
        constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}],
        options={"ftol": 1e-14, "maxiter": 1000},
    )
    w = np.clip(np.asarray(res.x, dtype=np.float64), 0.0, cap)
    return w / w.sum(), bool(res.success)


def min_variance(cov: Matrix, *, cap: float = 1.0) -> Weights:
    n = _check(cov, cap)
    w, ok = _slsqp(lambda x: float(x @ cov @ x), n, cap)
    return Weights(Method.MIN_VARIANCE, w, ok)


def max_sharpe(mu: Vector, cov: Matrix, *, rf: float = 0.0, cap: float = 1.0) -> Weights:
    """Tangency weights for expected returns ``mu`` and risk-free ``rf`` (same period)."""
    n = _check(cov, cap)
    excess = np.asarray(mu, dtype=np.float64) - rf
    notes = ["expected returns are historical means: their estimation error dominates"]
    if np.all(excess <= 0):
        notes.append("no asset's mean return exceeds the risk-free rate")

    def neg_sharpe(x: Vector) -> float:
        vol = float(np.sqrt(max(float(x @ cov @ x), 1e-30)))
        return -float(x @ excess) / vol

    w, ok = _slsqp(neg_sharpe, n, cap)
    return Weights(Method.MAX_SHARPE, w, ok, notes)


def risk_contributions(w: Vector, cov: Matrix) -> Vector:
    """Each asset's share of portfolio variance (sums to 1)."""
    var = float(w @ cov @ w)
    if var <= 0:
        return np.zeros_like(w)
    return np.asarray(w * (cov @ w) / var, dtype=np.float64)


def risk_parity(cov: Matrix, *, cap: float = 1.0) -> Weights:
    """Equal risk contribution: min ½yᵀΣy - (1/N)Σ log y over y > 0, then w = y / Σy."""
    n = _check(cov, cap)
    if np.any(np.diag(cov) <= 0):
        raise QPValidationError("risk parity needs every asset to have positive variance")
    x0 = 1.0 / np.sqrt(np.diag(cov))
    x0 = x0 / x0.sum()
    res = minimize(  # type: ignore[call-overload]
        lambda y: 0.5 * float(y @ cov @ y) - float(np.sum(np.log(y))) / n,
        x0,
        jac=lambda y: cov @ y - 1.0 / (n * y),
        method="L-BFGS-B",
        bounds=[(1e-12, None)] * n,
        options={"ftol": 1e-15, "gtol": 1e-12, "maxiter": 5000},
    )
    y = np.asarray(res.x, dtype=np.float64)
    w = y / y.sum()
    notes = [] if np.all(w <= cap + 1e-9) else [f"risk parity weights exceed the cap {cap}"]
    return Weights(Method.RISK_PARITY, w, bool(res.success), notes)


def _cluster_var(cov: Matrix, idx: Sequence[int]) -> float:
    sub = cov[np.ix_(idx, idx)]
    ivp = 1.0 / np.diag(sub)
    ivp = ivp / ivp.sum()
    return float(ivp @ sub @ ivp)


def hrp(cov: Matrix, *, cap: float = 1.0) -> Weights:
    """Hierarchical risk parity (López de Prado 2016): single linkage on √(½(1-rho)),
    quasi-diagonal order, recursive bisection with inverse-variance cluster weights."""
    n = _check(cov, cap)
    sd = np.sqrt(np.diag(cov))
    if np.any(sd <= 0):
        raise QPValidationError("HRP needs every asset to have positive variance")
    w = np.ones(n)
    if n > 1:
        corr = np.clip(cov / np.outer(sd, sd), -1.0, 1.0)
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
        np.fill_diagonal(dist, 0.0)
        order = [int(i) for i in leaves_list(linkage(squareform(dist, checks=False), "single"))]
        clusters = [order]
        while clusters:
            nxt = []
            for c in clusters:
                if len(c) < 2:
                    continue
                left, right = c[: len(c) // 2], c[len(c) // 2 :]
                vl, vr = _cluster_var(cov, left), _cluster_var(cov, right)
                alpha = 1.0 - vl / (vl + vr)
                w[left] *= alpha
                w[right] *= 1.0 - alpha
                nxt += [left, right]
            clusters = nxt
    w = w / w.sum()
    notes = [] if np.all(w <= cap + 1e-9) else [f"HRP weights exceed the cap {cap}"]
    return Weights(Method.HRP, w, True, notes)


def cvar(w: Vector, returns: Matrix, level: float = 0.95) -> float:
    """Historical CVaR (expected loss in the worst 1 - level tail) as a positive fraction:
    the Rockafellar-Uryasev value, which equals the LP objective of :func:`min_cvar`."""
    losses = -(np.asarray(returns, dtype=np.float64) @ w)
    t = losses.size
    tail = 1.0 - level
    k = int(np.floor(tail * t + 1e-9))
    s = np.sort(losses)[::-1]
    # VaR at ``level`` is the (k+1)-th largest loss; RU CVaR = VaR + E[(L - VaR)+] / tail
    var = s[min(k, t - 1)]
    return float(var + np.sum(np.maximum(s - var, 0.0)) / (tail * t))


def min_cvar(returns: Matrix, *, level: float = 0.95, cap: float = 1.0) -> Weights:
    """Rockafellar-Uryasev LP: min alpha + Σz/((1-beta)T)
    s.t. z ≥ -Rw - alpha, z ≥ 0, Σw = 1, 0 ≤ w ≤ cap."""
    r = np.asarray(returns, dtype=np.float64)
    t, n = r.shape
    if cap * n < 1 - TOL:
        raise QPValidationError(f"cap {cap} is too low for {n} assets to be fully invested")
    if not 0 < level < 1:
        raise QPValidationError("level must be in (0, 1)")
    # variables: w (n), z (t), alpha (1)
    c = np.concatenate([np.zeros(n), np.full(t, 1.0 / ((1.0 - level) * t)), [1.0]])
    a_ub = np.hstack([-r, -np.eye(t), -np.ones((t, 1))])
    b_ub = np.zeros(t)
    a_eq = np.concatenate([np.ones(n), np.zeros(t), [0.0]])[None, :]
    bounds = [(0.0, cap)] * n + [(0.0, None)] * t + [(None, None)]
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=[1.0], bounds=bounds, method="highs")
    if not res.success:
        return Weights(Method.MIN_CVAR, np.full(n, 1.0 / n), False, [str(res.message)])
    assert res.x is not None
    w = np.clip(np.asarray(res.x[:n], dtype=np.float64), 0.0, cap)
    return Weights(Method.MIN_CVAR, w / w.sum(), True)
