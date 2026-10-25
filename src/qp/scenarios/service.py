"""Wiring for apps: every configured scenario on today's holdings (ADR 0016, 6-9)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from qp.core import InstrumentId, ensure_utc
from qp.performance.history import LATEST
from qp.performance.service import Context
from qp.portfolio.holdings import build_report
from qp.risk.service import aggregate, holdings_of
from qp.risk.var import Holding
from qp.scenarios.definitions import Scenarios, load
from qp.scenarios.engine import ScenarioResult, apply_shock, history_days, replay, worst_window


@dataclass
class ScenarioReport:
    day: date
    value_try: Decimal
    results: list[ScenarioResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def scenario_report(
    ctx: Context,
    *,
    day: date,
    accounts: Collection[str] | None = None,
    known_at: datetime | None = None,
    classes: dict[InstrumentId, str] | None = None,
    scenarios: Scenarios | None = None,
    names: Collection[str] | None = None,
) -> ScenarioReport:
    known_at = ensure_utc(known_at) if known_at is not None else None
    as_of = known_at or LATEST
    cfg = ctx.settings.scenarios
    defs = scenarios if scenarios is not None else load(cfg.file)
    report = build_report(
        ctx.transactions,
        ctx.market,
        as_of=day,
        known_at=known_at,
        accounts=accounts,
        stale_after_days=ctx.settings.performance.stale_after_days,
    )
    held = aggregate(holdings_of(report))
    known = classes or {}

    def asset_class(h: Holding) -> str:
        return "cash" if h.instrument is None else known.get(h.instrument, "unknown")

    out = ScenarioReport(day, sum((h.value_try for h in held), Decimal(0)))
    if not report.complete:
        out.notes.append("some holdings have no price or rate today and are left out")
    if not defs.shocks and not defs.replays:
        out.notes.append(f"no scenarios defined in {cfg.file}")

    def wanted(name: str) -> bool:
        return names is None or name in names

    for s in defs.shocks:
        if wanted(s.name):
            out.results.append(apply_shock(held, s, asset_class))
    for r in defs.replays:
        if wanted(r.name):
            out.results.append(replay(held, r, ctx.market, as_of=as_of))
    days = history_days(day)
    for k in cfg.worst_windows:
        if wanted(f"worst {k}-day"):
            out.results.append(
                worst_window(
                    held,
                    days,
                    ctx.market,
                    as_of=as_of,
                    k=k,
                    min_observations=ctx.settings.risk.min_observations,
                )
            )
    return out
