"""Run the alert rules for a day: evaluate, store new events once, deliver them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from qp.alerts.rules import (
    AlertCandidate,
    Thresholds,
    data_alerts,
    news_alerts,
    performance_alerts,
)
from qp.alerts.store import deliver, record
from qp.core import assume_istanbul, istanbul_date
from qp.data.market import PreloadedMarketData
from qp.news.store import query as news_query
from qp.performance.history import LATEST
from qp.performance.metrics import Summary, View
from qp.performance.service import load_context, performance_report
from qp.platform.db import Database
from qp.platform.jobs import job_run
from qp.platform.notifier import Notifier, build_notifier
from qp.platform.settings import Settings
from qp.portfolio.holdings import build_report
from qp.risk.models import HeartbeatRow
from qp.strategies.promotion import deployments


@dataclass
class AlertRun:
    day: date
    candidates: list[AlertCandidate] = field(default_factory=list)
    new_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def thresholds(settings: Settings) -> Thresholds:
    a, r = settings.alerts, settings.risk
    return Thresholds(
        a.daily_loss,
        a.drawdown,
        a.var_level,
        a.stale_price_days,
        r.lookback_days,
        r.min_observations,
    )


def evaluate(settings: Settings, day: date) -> AlertRun:
    """Candidates for ``day`` (whole ledger, TRY view); nothing is stored or sent."""
    run = AlertRun(day)
    ctx = load_context(settings)
    if not ctx.transactions:
        run.notes.append("ledger is empty")
        return run
    t = thresholds(settings)
    report = performance_report(ctx, end=day, view=View.TRY, with_periods=False)
    if isinstance(report.summary, Summary):
        run.candidates += performance_alerts(report.summary, t)
    else:
        run.notes.append(f"performance unavailable: {report.summary.reason}")
    market = PreloadedMarketData(ctx.market, as_of=LATEST, until=day)
    holdings = build_report(ctx.transactions, market, as_of=day)
    run.candidates += data_alerts(holdings, t)
    return run


def run_alerts(
    settings: Settings,
    db: Database,
    *,
    day: date,
    notifier: Notifier | None = None,
    dry_run: bool = False,
) -> AlertRun:
    """Evaluate; unless ``dry_run``, store new events and send them (recorded as a job)."""
    if dry_run:
        return evaluate(settings, day)
    with job_run(db, "alerts", secrets=settings.secret_values()) as job:
        run = evaluate(settings, day)
        run.new_ids = record(db, run.candidates)
        run.errors = deliver(db, notifier or build_notifier(settings.telegram), run.new_ids)
        job.note(f"{day}: {len(run.candidates)} candidates, {len(run.new_ids)} new")
        job.lines.extend(run.notes)
        if run.errors:
            job.status = "partial"
            job.lines.extend(run.errors)
    return run


def run_news_alerts(
    settings: Settings,
    db: Database,
    *,
    day: date,
    notifier: Notifier | None = None,
    lookback_days: int = 3,
) -> AlertRun:
    """Holding-news alerts for items published in the last ``lookback_days``; sent once."""
    run = AlertRun(day)
    ctx = load_context(settings)
    market = PreloadedMarketData(ctx.market, as_of=LATEST, until=day)
    held = {
        str(r.valuation.instrument)
        for r in build_report(ctx.transactions, market, as_of=day).positions
    }
    if not held:
        run.notes.append("no open positions")
        return run
    since: datetime = assume_istanbul(datetime.combine(day - timedelta(days=lookback_days), time()))
    items = news_query(db, as_of=LATEST, since=since, entity_keys=sorted(held))
    run.candidates = news_alerts(items, held)
    run.new_ids = record(db, run.candidates)
    run.errors = deliver(db, notifier or build_notifier(settings.telegram), run.new_ids)
    return run


def run_heartbeat_check(
    settings: Settings,
    db: Database,
    *,
    now: datetime,
    notifier: Notifier | None = None,
    max_age_hours: int = 30,
) -> AlertRun:
    """Alert when a paper strategy's last heartbeat is older than ``max_age_hours`` on a
    weekday (ADR 0015, 7): the trader stopped running or crashed."""
    run = AlertRun(istanbul_date(now))
    if run.day.weekday() >= 5:
        run.notes.append("weekend: no heartbeat expected")
        return run
    for d in deployments(db):
        if d.status not in ("paper", "approved_live"):
            continue
        with db.session() as s:
            last = s.scalar(
                select(func.max(HeartbeatRow.created_at)).where(
                    HeartbeatRow.component == f"paper:{d.name}"
                )
            )
        if last is None or (now - last).total_seconds() > max_age_hours * 3600:
            seen = "never" if last is None else f"{last:%Y-%m-%d %H:%M} UTC"
            run.candidates.append(
                AlertCandidate(
                    "missing_heartbeat",
                    d.name,
                    run.day,
                    "critical",
                    f"Paper trader for {d.name} has not reported since {seen}",
                )
            )
    run.new_ids = record(db, run.candidates)
    run.errors = deliver(db, notifier or build_notifier(settings.telegram), run.new_ids)
    return run
