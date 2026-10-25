"""Pure signal functions (rule 8): closes known up to a bar -> target position at that bar.

One implementation, reused unchanged by research, the vectorized reference, the Nautilus
backtest, paper and live. Contract: ``signal(closes, **params)`` returns one target per
close, in [-1, 1] (fraction of the strategy's capital; negative = short), and the target at
index t depends only on ``closes[: t + 1]``. NaN means "not enough history": stay flat.
No I/O, no Nautilus, no clock.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

type Signal = Callable[..., list[float]]


def _closes(closes: Sequence[float]) -> np.ndarray:
    a = np.asarray(closes, dtype=np.float64)
    if a.ndim != 1 or np.any(a <= 0) or not np.all(np.isfinite(a)):
        raise ValueError("closes must be positive and finite")
    return a


def _sma(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(a.size, np.nan)
    if a.size >= n:
        c = np.cumsum(np.insert(a, 0, 0.0))
        out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def sma_cross(
    closes: Sequence[float], *, fast: int = 20, slow: int = 50, short: bool = False
) -> list[float]:
    """Long (1) when the fast moving average is above the slow one, else flat (or short)."""
    if not 1 <= fast < slow:
        raise ValueError("need 1 <= fast < slow")
    a = _closes(closes)
    f, s = _sma(a, fast), _sma(a, slow)
    target = np.where(np.isnan(s), 0.0, np.where(f > s, 1.0, -1.0 if short else 0.0))
    return [float(x) for x in target]


def momentum(closes: Sequence[float], *, lookback: int = 60, short: bool = False) -> list[float]:
    """Long when the close is above its value ``lookback`` bars ago, else flat (or short)."""
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    a = _closes(closes)
    out = np.zeros(a.size)
    past = a[:-lookback] if lookback < a.size else np.array([])
    if past.size:
        up = a[lookback:] > past
        out[lookback:] = np.where(up, 1.0, -1.0 if short else 0.0)
    return [float(x) for x in out]


def buy_and_hold(closes: Sequence[float]) -> list[float]:
    """Always fully long: the baseline every strategy is compared with."""
    _closes(closes)
    return [1.0] * len(closes)


@dataclass(frozen=True, slots=True)
class SignalSpec:
    name: str
    fn: Signal
    defaults: dict[str, Any]
    grid: dict[str, list[Any]]  # walk-forward parameter grid


REGISTRY: dict[str, SignalSpec] = {
    "sma_cross": SignalSpec(
        "sma_cross", sma_cross, {"fast": 20, "slow": 50}, {"fast": [5, 10, 20], "slow": [50, 100]}
    ),
    "momentum": SignalSpec("momentum", momentum, {"lookback": 60}, {"lookback": [20, 60, 120]}),
    "buy_and_hold": SignalSpec("buy_and_hold", buy_and_hold, {}, {}),
}


def get(name: str) -> SignalSpec:
    if name not in REGISTRY:
        raise KeyError(f"unknown signal {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name]
