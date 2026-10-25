"""qp.strategies.signals — pure signal functions (no Nautilus, DB, lake, network or clock).
See :mod:`qp.strategies.signals.core` and the ``signals-pure`` import contract."""

from qp.strategies.signals.core import (
    REGISTRY,
    SignalSpec,
    buy_and_hold,
    get,
    momentum,
    sma_cross,
)

__all__ = ["REGISTRY", "SignalSpec", "buy_and_hold", "get", "momentum", "sma_cross"]
