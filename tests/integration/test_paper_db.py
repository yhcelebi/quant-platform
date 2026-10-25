"""Paper trading end to end on a throwaway database (ADR 0015): promotions with evidence,
daily cycles on data known day by day, gate, kill switch, flatten, reconciliation, and a
paper-vs-backtest report that must match exactly."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from qp.backtest.service import load_series, run_walk_forward
from qp.core import assume_istanbul
from qp.execution.service import run_paper
from qp.platform import Database, load_settings
from qp.reporting.paper import paper_vs_backtest
from qp.risk import gate
from qp.strategies.promotion import PromotionError, deploy, deployments, promote

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


def weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_paper_trading_flow(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    days = weekdays(date(2025, 1, 2), 140)
    manual = tmp_path / "manual"
    manual.mkdir()
    rates = [40 * (1 + 0.002 * i + 0.03 * math.sin(i / 6)) for i in range(len(days))]
    (manual / "fx.csv").write_text(
        "valid_date,currency,rate_try,source,known_time\n"
        + "".join(f"{d},USD,{r:.4f},synthetic,\n" for d, r in zip(days, rates, strict=True))
    )
    url = migrated_db.engine.url
    for key, value in {
        "QP_DB__HOST": url.host, "QP_DB__PORT": str(url.port), "QP_DB__NAME": url.database,
        "QP_DB__USER": url.username, "QP_DB__PASSWORD": url.password,
        "QP_MANUAL__DIR": str(manual), "QP_LAKE__ROOT": str(tmp_path / "lake"),
    }.items():  # fmt: skip
        monkeypatch.setenv(key, str(value))
    settings = load_settings(config_dir=REPO / "configs", env_file=None)
    db = migrated_db

    deploy(
        db,
        "usd-sma",
        signal="sma_cross",
        params={"fast": 3, "slow": 10},
        costs="fx_try",
        capital=Decimal(100000),
        fx="USD",
    )
    with pytest.raises(PromotionError, match="cannot move"):
        promote(db, "usd-sma", "paper")
    # evidence: a walk-forward over the first 100 bars, as known on day 100
    known = assume_istanbul(datetime.combine(days[100], time(23, 59, 59)))
    series = load_series(settings, instrument=None, fx="USD", as_of=known, start=None, end=days[99])
    _, folder = run_walk_forward(
        settings, series=series, signal="sma_cross", costs="fx_try", train=40, test=20, repo=REPO
    )
    promote(db, "usd-sma", "backtested", evidence=str(folder))
    promote(db, "usd-sma", "paper", note="test")
    with pytest.raises(PromotionError, match="owner"):
        promote(db, "usd-sma", "approved_live")
    with db.engine.begin() as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("INSERT INTO trading.promotions (strategy_id, status) VALUES (1, 'approved_live')")
        )

    def cycle(day: date) -> list[str]:
        now = assume_istanbul(datetime.combine(day + timedelta(days=1), time(0, 30)))
        return [r.halted or "" for r in run_paper(settings, db, now=now)]

    for d in days[100:120]:
        cycle(d)
    assert cycle(days[119]) == [""]  # rerun of the same day: no-op
    report = paper_vs_backtest(settings, db, "usd-sma", now=datetime(2026, 1, 1, tzinfo=UTC))
    assert len(report.days) == 20
    assert report.max_abs_diff == 0  # same signal, sizing and costs: identical to the cent
    with db.session() as s:
        recs = s.execute(text("SELECT bool_and(ok) FROM trading.reconciliations")).scalar_one()
    assert recs is True

    gate.set_halt(db, "global", halted_=True, reason="test halt")
    assert gate.halted(db, "usd-sma") == "global: test halt"
    cycle(days[120])
    gate.set_halt(db, "global", halted_=False, reason="test over")
    gate.set_halt(db, "flatten:usd-sma", halted_=True, reason="flatten requested")
    for d in days[121:124]:
        cycle(d)
    [dep] = deployments(db)
    assert gate.halted(db, dep.name) == "flattened on request"
    with db.session() as s:
        qty = s.execute(
            text("SELECT quantity FROM trading.positions_snapshot ORDER BY bar_date DESC LIMIT 1")
        ).scalar_one()
    assert qty == 0
    with db.engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text("DELETE FROM trading.fills"))


def test_trading_page_kill_switch(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from streamlit.testing.v1 import AppTest

    url = migrated_db.engine.url
    for key, value in {
        "QP_DB__HOST": url.host, "QP_DB__PORT": str(url.port), "QP_DB__NAME": url.database,
        "QP_DB__USER": url.username, "QP_DB__PASSWORD": url.password,
        "QP_LAKE__ROOT": str(tmp_path / "lake"),
    }.items():  # fmt: skip
        monkeypatch.setenv(key, str(value))
    at = AppTest.from_file(str(REPO / "apps" / "terminal" / "app.py"), default_timeout=30).run()
    at.switch_page("pages/trading.py").run()
    assert not at.exception
    halt = next(b for b in at.button if b.label == "HALT ALL")
    halt.click().run()
    assert not at.exception
    assert gate.halted(migrated_db, "any") == "global: halted from the terminal"
