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

__all__ = [
    "Drawdown",
    "correlation",
    "drawdown_series",
    "drawdowns",
    "historical_es",
    "historical_var",
    "parametric_es",
    "parametric_var",
    "volatility",
]
