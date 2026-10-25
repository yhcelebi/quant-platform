"""Bias checks (ADR 0014, 2-3): signals never see the future, and trades happen only after
the bar whose close produced the signal."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from qp.backtest.data import PriceSeries
from qp.backtest.engine import run_nautilus
from qp.backtest.reference import simulate
from qp.core import TRY, InstrumentId
from qp.strategies.costs import COSTLESS
from qp.strategies.signals import REGISTRY

closes_strategy = st.lists(st.floats(1.0, 1000.0), min_size=30, max_size=120)


@given(closes_strategy, st.data())
@settings(max_examples=60)
def test_signals_ignore_future_closes(closes: list[float], data: st.DataObject) -> None:
    t = data.draw(st.integers(0, len(closes) - 1))
    future = data.draw(
        st.lists(st.floats(1.0, 1000.0), min_size=len(closes) - t - 1, max_size=len(closes) - t - 1)
    )
    changed = closes[: t + 1] + future
    for spec in REGISTRY.values():
        a = spec.fn(closes, **spec.defaults)
        b = spec.fn(changed, **spec.defaults)
        assert a[: t + 1] == b[: t + 1], spec.name


def test_trades_come_after_their_signal_bar() -> None:
    s = synthetic_series()
    for engine in (simulate, run_nautilus):
        r = engine(
            s,
            signal="sma_cross",
            params={"fast": 5, "slow": 20},
            capital=Decimal(100000),
            costs=COSTLESS,
        )
        index = {d: i for i, d in enumerate(r.days)}
        for f in r.fills:
            i = index[f.day]
            assert i >= 1
            held_before = r.position[i - 1]
            wanted_sign = 1 if r.targets[i - 1] > 0 else 0
            # the fill at bar i realises the target decided at bar i - 1, never bar i's own
            assert (held_before + f.quantity > 0) == bool(wanted_sign)


def synthetic_series(n: int = 120, seed: int = 11) -> PriceSeries:
    rng = np.random.default_rng(seed)
    days: list[date] = []
    closes: list[Decimal] = []
    known: list[datetime] = []
    d, p = date(2025, 1, 2), 50.0
    while len(days) < n:
        if d.weekday() < 5:
            p *= float(np.exp(rng.normal(0, 0.02)))
            days.append(d)
            closes.append(Decimal(f"{p:.2f}"))
            known.append(datetime(d.year, d.month, d.day, 15, 30, tzinfo=UTC))
        d += timedelta(days=1)
    return PriceSeries(InstrumentId.parse("SYN.BIST"), TRY, days, closes, known, "synthetic")
