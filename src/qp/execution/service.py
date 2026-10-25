"""Wiring for the trader app and CLI: run every tradable deployment once (ADR 0015)."""

from __future__ import annotations

from datetime import datetime

from qp.core import istanbul_date
from qp.data.service import market_data
from qp.execution.paper import CycleReport, run_cycle
from qp.platform.db import Database
from qp.platform.settings import Settings
from qp.risk import gate
from qp.strategies.costs import from_settings
from qp.strategies.promotion import deployments

TRADABLE = frozenset({"paper", "approved_live"})


def run_paper(settings: Settings, db: Database, *, now: datetime) -> list[CycleReport]:
    """One paper cycle for every strategy in a tradable status (live never runs here)."""
    market = market_data(settings)
    out: list[CycleReport] = []
    for d in deployments(db):
        if d.status not in TRADABLE:
            continue
        costs = from_settings(d.costs, settings.backtest.costs[d.costs])
        out.append(
            run_cycle(
                db,
                d,
                market=market,
                costs=costs,
                as_of=now,
                today=istanbul_date(now),
                mode="paper",
                limits=gate.Limits(),
            )
        )
    return out
