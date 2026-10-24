"""Sharpe ratio and overfitting checks (ADR 0013, 5-6).

- Probabilistic Sharpe ratio (Bailey & López de Prado, 2012).
- Deflated Sharpe ratio (Bailey & López de Prado, 2014).
- Probability of backtest overfitting by combinatorially symmetric cross-validation
  (Bailey, Borwein, López de Prado & Zhu, 2017).

Sharpe ratios inside PSR/DSR are per period (not annualized); kurtosis is not excess.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import kurtosis, norm, skew

from qp.core import QPValidationError

EULER_GAMMA = 0.5772156649015329


def sharpe(returns: Sequence[float], *, periods_per_year: int = 252, rf: float = 0.0) -> float:
    """Annualized Sharpe: mean(r - rf) / std(r, ddof 1) x sqrt(periods)."""
    a = np.asarray(returns, dtype=np.float64) - rf
    if a.size < 2 or np.std(a, ddof=1) == 0:
        raise QPValidationError("Sharpe needs at least two non-constant returns")
    return float(np.mean(a) / np.std(a, ddof=1) * math.sqrt(periods_per_year))


def _moments(returns: Sequence[float]) -> tuple[float, float, float, int]:
    a = np.asarray(returns, dtype=np.float64)
    if a.size < 3 or np.std(a, ddof=1) == 0:
        raise QPValidationError("PSR needs at least three non-constant returns")
    sr = float(np.mean(a) / np.std(a, ddof=1))
    return sr, float(skew(a)), float(kurtosis(a, fisher=False)), int(a.size)


def probabilistic_sharpe(returns: Sequence[float], benchmark_sr: float = 0.0) -> float:
    """P(true per-period SR > benchmark_sr) given the sample's length, skew and kurtosis."""
    sr, g3, g4, n = _moments(returns)
    denom = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    if denom <= 0:
        raise QPValidationError("PSR undefined for these moments")
    return float(norm.cdf((sr - benchmark_sr) * math.sqrt(n - 1) / math.sqrt(denom)))


def expected_max_sharpe(trials: int, trial_sr_variance: float) -> float:
    """Expected maximum per-period SR of ``trials`` unskilled strategies:
    sqrt(V) ((1 - g) Phi^-1(1 - 1/N) + g Phi^-1(1 - 1/(N e)))."""
    if trials < 2:
        raise QPValidationError("deflation needs at least two trials")
    if trial_sr_variance < 0:
        raise QPValidationError("variance must be >= 0")
    return math.sqrt(trial_sr_variance) * (
        (1 - EULER_GAMMA) * norm.ppf(1 - 1 / trials)
        + EULER_GAMMA * norm.ppf(1 - 1 / (trials * math.e))
    )


def deflated_sharpe(returns: Sequence[float], *, trials: int, trial_sr_variance: float) -> float:
    """PSR against the expected maximum Sharpe of ``trials`` strategies (per period)."""
    return probabilistic_sharpe(returns, expected_max_sharpe(trials, trial_sr_variance))


@dataclass(frozen=True, slots=True)
class PBO:
    probability: float  # share of splits whose in-sample winner ranks at or below median OOS
    logits: list[float]
    splits: int
    blocks: int


def _block_sharpe(m: np.ndarray) -> np.ndarray:
    sd = m.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(sd > 0, m.mean(axis=0) / sd, -np.inf)
    return out


def pbo(returns: np.ndarray, *, blocks: int = 16) -> PBO:
    """CSCV on a T x N matrix (rows: periods, columns: strategy configurations)."""
    m = np.asarray(returns, dtype=np.float64)
    if m.ndim != 2 or m.shape[1] < 2:
        raise QPValidationError("PBO needs a T x N matrix with N >= 2 configurations")
    if blocks % 2 or blocks < 2 or m.shape[0] < 2 * blocks:
        raise QPValidationError("blocks must be even and leave at least two rows per block")
    parts = np.array_split(np.arange(m.shape[0]), blocks)
    n = m.shape[1]
    logits: list[float] = []
    for chosen in itertools.combinations(range(blocks), blocks // 2):
        ins = np.concatenate([parts[i] for i in chosen])
        oos = np.concatenate([parts[i] for i in range(blocks) if i not in chosen])
        best = int(np.argmax(_block_sharpe(m[ins])))
        oos_sr = _block_sharpe(m[oos])
        rank = int(np.sum(oos_sr < oos_sr[best])) + 1  # 1 = worst, n = best
        omega = rank / (n + 1)
        logits.append(math.log(omega / (1 - omega)))
    prob = sum(1 for x in logits if x <= 0) / len(logits)
    return PBO(prob, logits, len(logits), blocks)
