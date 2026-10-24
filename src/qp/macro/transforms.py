"""Transformations of a point-in-time series (ADR 0012, 2).

Input: an ascending history already limited to facts known at ``as_of`` (as returned by
``SeriesData.history``), so no output can use later data. Output: ``(valid_date, value)``
pairs, float64 (macro analytics; converted at the qp.core boundary). A period whose
predecessor is missing yields nothing, never an estimate.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import StrEnum

from qp.core import QPValidationError, to_float64
from qp.data.series import SeriesPoint

type Obs = tuple[date, float]


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


PERIODS_PER_YEAR = {
    Frequency.DAILY: 252,
    Frequency.WEEKLY: 52,
    Frequency.MONTHLY: 12,
    Frequency.QUARTERLY: 4,
    Frequency.ANNUAL: 1,
}


def observations(points: Sequence[SeriesPoint]) -> list[Obs]:
    return [(p.valid_date, to_float64(p.value)) for p in points]


def _period_index(d: date, freq: Frequency) -> int:
    """A counter that increases by one per period, so gaps can be detected."""
    if freq is Frequency.MONTHLY:
        return d.year * 12 + d.month - 1
    if freq is Frequency.QUARTERLY:
        return d.year * 4 + (d.month - 1) // 3
    if freq is Frequency.ANNUAL:
        return d.year
    if freq is Frequency.WEEKLY:
        return d.toordinal() // 7
    return d.toordinal()  # daily: by observation, see _lagged


def _lagged(obs: Sequence[Obs], k: int, freq: Frequency) -> list[tuple[date, float, float]]:
    """(date, value, value k periods earlier) where the earlier period exists."""
    if k < 1:
        raise QPValidationError("k must be >= 1")
    if freq is Frequency.DAILY:  # business-day series: k observations back
        return [(obs[i][0], obs[i][1], obs[i - k][1]) for i in range(k, len(obs))]
    by_period = {_period_index(d, freq): v for d, v in obs}
    out = []
    for d, v in obs:
        prev = by_period.get(_period_index(d, freq) - k)
        if prev is not None:
            out.append((d, v, prev))
    return out


def level(obs: Sequence[Obs]) -> list[Obs]:
    return list(obs)


def change(obs: Sequence[Obs], k: int, freq: Frequency) -> list[Obs]:
    """Difference over k periods (e.g. a rate's change in percentage points)."""
    return [(d, v - p) for d, v, p in _lagged(obs, k, freq)]


def pct_change(obs: Sequence[Obs], k: int, freq: Frequency) -> list[Obs]:
    """Fractional change over k periods: MoM (k=1 monthly), YoY (k=12 monthly)."""
    return [(d, v / p - 1) for d, v, p in _lagged(obs, k, freq) if p != 0]


def annualized(obs: Sequence[Obs], k: int, freq: Frequency) -> list[Obs]:
    """(1 + r_k) ** (periods per year / k) - 1, e.g. a 3-month change at an annual rate."""
    n = PERIODS_PER_YEAR[freq]
    return [(d, (1 + r) ** (n / k) - 1) for d, r in pct_change(obs, k, freq) if r > -1]


def rolling_mean(obs: Sequence[Obs], k: int) -> list[Obs]:
    """Mean of the last k observations, from the k-th observation on."""
    if k < 1:
        raise QPValidationError("k must be >= 1")
    return [
        (obs[i][0], sum(v for _, v in obs[i - k + 1 : i + 1]) / k) for i in range(k - 1, len(obs))
    ]


def rebased(obs: Sequence[Obs], base: date) -> list[Obs]:
    """100 at the last observation on or before ``base``."""
    anchor = [v for d, v in obs if d <= base]
    if not anchor or anchor[-1] == 0:
        raise QPValidationError(f"no non-zero observation on or before {base}")
    b = anchor[-1]
    return [(d, 100 * v / b) for d, v in obs]


class Transform(StrEnum):
    LEVEL = "level"
    MOM = "mom"
    YOY = "yoy"
    CHANGE = "change"
    ANNUALIZED_3M = "annualized_3m"
    ROLLING_12 = "rolling_12"


def apply(transform: Transform, obs: Sequence[Obs], freq: Frequency) -> list[Obs]:
    """The named transformations offered in the terminal and CLI."""
    yoy_k = {Frequency.MONTHLY: 12, Frequency.QUARTERLY: 4, Frequency.WEEKLY: 52}.get(freq)
    if transform is Transform.LEVEL:
        return level(obs)
    if transform is Transform.MOM:
        return pct_change(obs, 1, freq)
    if transform is Transform.YOY:
        if freq is Frequency.DAILY or freq is Frequency.ANNUAL:
            return pct_change(obs, 252 if freq is Frequency.DAILY else 1, freq)
        assert yoy_k is not None
        return pct_change(obs, yoy_k, freq)
    if transform is Transform.CHANGE:
        return change(obs, 1, freq)
    if transform is Transform.ANNUALIZED_3M:
        k = {Frequency.MONTHLY: 3, Frequency.WEEKLY: 13, Frequency.DAILY: 63}.get(freq, 1)
        return annualized(obs, k, freq)
    return rolling_mean(obs, 12)
