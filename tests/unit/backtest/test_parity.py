"""Backtest parity (ADR 0014, 7): NautilusTrader and the reference loop agree to the cent."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from qp.backtest.data import PriceSeries
from qp.backtest.engine import run_nautilus
from qp.backtest.reference import simulate
from qp.core import TRY, InstrumentId, QPValidationError
from qp.strategies.costs import CostModel, CostRates


def series(n: int = 160, seed: int = 3) -> PriceSeries:
    rng = np.random.default_rng(seed)
    days: list[date] = []
    closes: list[Decimal] = []
    known: list[datetime] = []
    d, p = date(2025, 1, 2), 100.0
    while len(days) < n:
        if d.weekday() < 5:
            p *= float(np.exp(rng.normal(0.0005, 0.02)))
            days.append(d)
            closes.append(Decimal(f"{p:.2f}"))
            known.append(datetime(d.year, d.month, d.day, 15, 30, tzinfo=UTC))
        d += timedelta(days=1)
    return PriceSeries(InstrumentId.parse("TEST.BIST"), TRY, days, closes, known, "synthetic")


COSTS = CostModel(
    "test",
    (
        CostRates(
            date(2000, 1, 1),
            Decimal("0.0015"),
            Decimal("5"),
            Decimal("0.05"),
            Decimal("0.0005"),
            Decimal("0.0005"),
        ),
        CostRates(
            date(2025, 4, 1),
            Decimal("0.0010"),
            Decimal("5"),
            Decimal("0.05"),
            Decimal("0.0005"),
            Decimal("0.0005"),
        ),
    ),
)


@pytest.mark.parametrize(
    ("signal", "params"),
    [("sma_cross", {"fast": 5, "slow": 20}), ("momentum", {"lookback": 10}), ("buy_and_hold", {})],
)
def test_nautilus_matches_reference_to_the_cent(signal: str, params: dict[str, int]) -> None:
    s = series()
    ref = simulate(s, signal=signal, params=params, capital=Decimal(100000), costs=COSTS)
    nt = run_nautilus(s, signal=signal, params=params, capital=Decimal(100000), costs=COSTS)
    assert ref.fills
    assert nt.fills == ref.fills  # same days, quantities, prices and fees (dated rates)
    assert nt.cash_end == ref.cash_end  # Nautilus' own account balance
    assert nt.equity == ref.equity
    assert nt.targets == ref.targets
    assert ref.total_fees > 0


def test_fees_use_the_rates_in_force_on_the_fill_date() -> None:
    s = series()
    ref = simulate(
        s, signal="sma_cross", params={"fast": 5, "slow": 20}, capital=Decimal(100000), costs=COSTS
    )
    for f in ref.fills:
        assert f.fee == COSTS.fee(f.quantity * f.price, f.day)
    assert {COSTS.rates(f.day).commission for f in ref.fills} == {
        Decimal("0.0015"),
        Decimal("0.0010"),
    }


def test_short_targets_are_refused() -> None:
    s = series()
    with pytest.raises(QPValidationError, match="long-only"):
        simulate(
            s,
            signal="momentum",
            params={"lookback": 10, "short": True},
            capital=Decimal(100000),
            costs=COSTS,
        )


def test_walk_forward_windows_and_overfitting_stats() -> None:
    from qp.backtest.walkforward import walk_forward

    s = series(n=400, seed=5)
    wf = walk_forward(s, signal="sma_cross", capital=Decimal(100000), costs=COSTS)
    # windows start at bars 0 and 63 (0 + 315 <= 400, 63 + 315 <= 400, 126 + 315 > 400)
    assert len(wf.windows) == 2
    assert len(wf.oos_returns) == 2 * 63
    assert wf.oos_days[0] == s.days[252]
    assert len(wf.grid) == 6  # fast {5, 10, 20} x slow {50, 100}
    assert wf.metrics is not None
    assert wf.metrics.exposure > 0  # counted over the test days
    assert wf.pbo is not None
    assert 0 <= wf.pbo <= 1
    if wf.dsr is not None and wf.psr is not None:
        assert wf.dsr <= wf.psr  # deflating for 6 trials can only lower confidence


def test_metrics_compound_returns_by_hand() -> None:
    """+10%, -10%, +10%: wealth 1.1, 0.99, 1.089; total 8.9%; drawdown 0.99/1.1 - 1 = -10%."""
    from qp.backtest.walkforward import metrics_of

    m = metrics_of([0.1, -0.1, 0.1], first=date(2025, 1, 1), last=date(2025, 1, 4))
    assert m.total_return == pytest.approx(0.089)
    assert m.max_drawdown == pytest.approx(-0.1)
    assert m.cagr is None  # under a year: not annualized


def test_no_trade_band_skips_drift_but_not_exits() -> None:
    from qp.strategies.nautilus import order_delta

    # want 1000 units at 10 with equity 10000: holding 995 -> 5 units = 50 < 1% of 10000
    assert (
        order_delta(1.0, Decimal(10000), Decimal(10), Decimal(995), Decimal(0), Decimal("0.01"))
        == 0
    )
    assert (
        order_delta(1.0, Decimal(10000), Decimal(10), Decimal(900), Decimal(0), Decimal("0.01"))
        == 100
    )
    assert (
        order_delta(0.0, Decimal(10000), Decimal(10), Decimal(5), Decimal(0), Decimal("0.01")) == -5
    )


def test_results_store_and_backtests_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from streamlit.testing.v1 import AppTest

    from qp.backtest.results import list_runs, read_summary, store
    from qp.backtest.walkforward import sim_metrics
    from qp.platform import load_settings

    repo = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("QP_LAKE__ROOT", str(tmp_path / "lake"))
    settings = load_settings(config_dir=repo / "configs", env_file=None)
    s = series()
    sim = run_nautilus(
        s, signal="sma_cross", params={"fast": 5, "slow": 20}, capital=Decimal(100000), costs=COSTS
    )
    spec = {
        "signal": "sma_cross",
        "params": {},
        "instrument": "TEST.BIST",
        "source": "synthetic",
        "costs": "costless",
        "lag_bars": 1,
    }
    folder = store(
        settings, kind="backtest", spec=spec, metrics=sim_metrics(sim), sim=sim, repo=repo
    )
    summary = read_summary(folder)
    assert summary["valid"] is False  # costless runs are labelled invalid
    assert list_runs(settings) == [folder]
    at = AppTest.from_file(str(repo / "apps" / "terminal" / "app.py"), default_timeout=30).run()
    at.switch_page("pages/backtests.py").run()
    assert not at.exception
    assert any("no cost model" in e.value for e in at.error)
