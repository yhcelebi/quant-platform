"""qp.stats — Pure statistics (float64). No I/O.

May import: core only. See docs/ARCHITECTURE.md and .importlinter.
"""

from qp.stats.descriptive import (
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
from qp.stats.inference import (
    BootstrapCI,
    Cointegration,
    Garch,
    Regression,
    Stationarity,
    bootstrap,
    engle_granger,
    garch11,
    ols,
    stationarity,
)
from qp.stats.sharpe import (
    PBO,
    deflated_sharpe,
    expected_max_sharpe,
    pbo,
    probabilistic_sharpe,
    sharpe,
)

__all__ = [
    "PBO",
    "BootstrapCI",
    "Cointegration",
    "Drawdown",
    "Garch",
    "Regression",
    "Stationarity",
    "bootstrap",
    "correlation",
    "deflated_sharpe",
    "drawdown_series",
    "drawdowns",
    "engle_granger",
    "expected_max_sharpe",
    "garch11",
    "historical_es",
    "historical_var",
    "ols",
    "parametric_es",
    "parametric_var",
    "pbo",
    "probabilistic_sharpe",
    "sharpe",
    "stationarity",
    "volatility",
]
