"""Pairwise correlation of holdings' daily TRY returns, on days where both are fresh."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from qp.data.market import MarketHistory
from qp.risk.var import Holding, asset_returns
from qp.stats import correlation


@dataclass(frozen=True, slots=True)
class CorrelationMatrix:
    names: list[str]
    values: list[list[float]]  # nan where fewer than ``min_common`` shared fresh days
    common_days: list[list[int]]


def correlations(
    holdings: Sequence[Holding],
    days: Sequence[date],
    market: MarketHistory,
    *,
    as_of: datetime,
    min_common: int = 20,
) -> CorrelationMatrix:
    series = [asset_returns(h, days, market, as_of=as_of) for h in holdings]
    n = len(holdings)
    values = [[math.nan] * n for _ in range(n)]
    common = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            (ri, mi), (rj, mj) = series[i], series[j]
            idx = [k for k in range(len(days)) if mi[k] and mj[k]]
            common[i][j] = common[j][i] = len(idx)
            if len(idx) >= max(min_common, 3):
                c = 1.0 if i == j else correlation([ri[k] for k in idx], [rj[k] for k in idx])
                values[i][j] = values[j][i] = c
    return CorrelationMatrix([h.name for h in holdings], values, common)
