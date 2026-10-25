"""Workers entry point: ``make workers`` (python -m apps.workers.main).

Jobs (Europe/Istanbul times; job logic lives in qp, every run is recorded in ops.job_runs):

- heartbeat      every 60 s
- tcmb_fx        Mon-Fri 15:45 and 18:15: fetch new TCMB bulletins, then quality checks
- fred           daily 07:00, only with QP_SOURCES__FRED__API_KEY
- evds           daily 17:30, only with QP_SOURCES__EVDS__API_KEY and configured series
- alerts         Mon-Fri 18:30 (after the TCMB retry): alert rules and thesis reviews due,
                 new events sent once
- news           every sources.news.poll_minutes (30): official feeds, then holding-news alerts
- fred_calendar  daily 07:10, only with the FRED key: release dates into the calendar
- paper          Mon-Fri 18:45 (after alerts): one paper cycle per strategy in paper status
- heartbeat_chk  Mon-Fri hourly 09:00-21:00: alert if a paper strategy stopped reporting

A failing job is logged and recorded; it never stops the scheduler or other jobs.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from qp.alerts.service import run_alerts, run_heartbeat_check, run_news_alerts
from qp.core import SystemClock, istanbul_date
from qp.data import service
from qp.execution.service import run_paper
from qp.news.service import fetch_news
from qp.platform import Database, Settings, get_logger, init_platform
from qp.platform.notifier import build_notifier
from qp.reporting.daily import send_review_alerts

log = get_logger("apps.workers")
TZ = "Europe/Istanbul"


def heartbeat(settings: Settings) -> None:
    log.info("workers.heartbeat", mode=str(settings.mode))


def tcmb_fx(settings: Settings) -> None:
    with Database.from_settings(settings.db) as db:
        report = service.fetch_tcmb(settings, db)
        log.info("workers.tcmb_fx", rows=report.rows_written, errors=len(report.errors))
        bad = [r for r in service.run_quality(settings, db) if r.status != "ok"]
        log.info("workers.quality", not_ok=[f"{r.subject} {r.check}" for r in bad])


def fred(settings: Settings) -> None:
    with Database.from_settings(settings.db) as db:
        report = service.fetch_fred(settings, db)
        log.info("workers.fred", rows=report.rows_written, errors=len(report.errors))


def evds(settings: Settings) -> None:
    with Database.from_settings(settings.db) as db:
        report = service.fetch_evds(settings, db)
        log.info("workers.evds", rows=report.rows_written, errors=len(report.errors))


def alerts(settings: Settings) -> None:
    with Database.from_settings(settings.db) as db:
        day = istanbul_date(SystemClock().now())
        result = run_alerts(settings, db, day=day)
        reviews = send_review_alerts(db, day, build_notifier(settings.telegram))
        log.info("workers.thesis_reviews", new=len(reviews))
        log.info(
            "workers.alerts",
            candidates=len(result.candidates),
            new=len(result.new_ids),
            errors=len(result.errors),
        )


def news(settings: Settings) -> None:
    with Database.from_settings(settings.db) as db:
        report = fetch_news(settings, db)
        sent = run_news_alerts(settings, db, day=istanbul_date(SystemClock().now()))
        log.info(
            "workers.news", new=report.new, errors=len(report.errors), alerts=len(sent.new_ids)
        )


def fred_calendar(settings: Settings) -> None:
    with Database.from_settings(settings.db) as db:
        report = service.fetch_fred_calendar(settings, db)
        log.info("workers.fred_calendar", rows=report.rows_written, errors=len(report.errors))


def paper(settings: Settings) -> None:
    with Database.from_settings(settings.db) as db:
        reports = run_paper(settings, db, now=SystemClock().now())
        log.info(
            "workers.paper",
            strategies=len(reports),
            halted=[r.strategy for r in reports if r.halted],
        )


def heartbeat_check(settings: Settings) -> None:
    with Database.from_settings(settings.db) as db:
        result = run_heartbeat_check(settings, db, now=SystemClock().now())
        log.info("workers.heartbeat_check", missing=len(result.candidates))


def _guard(job_id: str, func: Callable[[], None]) -> Callable[[], None]:
    def run() -> None:
        try:
            func()
        except Exception as exc:  # recorded in ops.job_runs by the service; keep scheduling
            log.error("workers.job_failed", job=job_id, error=f"{type(exc).__name__}: {exc}")

    return run


def jobs(settings: Settings) -> dict[str, tuple[Callable[[], None], BaseTrigger]]:
    """Job id -> (callable, trigger). Sources without keys are not scheduled."""
    out: dict[str, tuple[Callable[[], None], BaseTrigger]] = {
        "heartbeat": (lambda: heartbeat(settings), IntervalTrigger(seconds=60, timezone=UTC)),
    }
    src = settings.sources
    if src.tcmb.enabled:
        out["tcmb_fx"] = (
            lambda: tcmb_fx(settings),
            CronTrigger.from_crontab("45 15 * * mon-fri", timezone=TZ),
        )
        out["tcmb_fx_retry"] = (
            lambda: tcmb_fx(settings),
            CronTrigger.from_crontab("15 18 * * mon-fri", timezone=TZ),
        )
    if src.fred.api_key is not None and src.fred.api_key.get_secret_value():
        out["fred"] = (lambda: fred(settings), CronTrigger.from_crontab("0 7 * * *", timezone=TZ))
    if src.evds.api_key is not None and src.evds.api_key.get_secret_value() and src.evds.series:
        out["evds"] = (lambda: evds(settings), CronTrigger.from_crontab("30 17 * * *", timezone=TZ))
    if any(f.enabled for f in src.news.feeds):
        out["news"] = (
            lambda: news(settings),
            IntervalTrigger(minutes=src.news.poll_minutes, timezone=UTC),
        )
    if src.fred.api_key is not None and src.fred.api_key.get_secret_value():
        out["fred_calendar"] = (
            lambda: fred_calendar(settings),
            CronTrigger.from_crontab("10 7 * * *", timezone=TZ),
        )
    out["paper"] = (
        lambda: paper(settings),
        CronTrigger.from_crontab("45 18 * * mon-fri", timezone=TZ),
    )
    out["heartbeat_check"] = (
        lambda: heartbeat_check(settings),
        CronTrigger.from_crontab("0 9-21 * * mon-fri", timezone=TZ),
    )
    if settings.alerts.enabled:
        out["alerts"] = (
            lambda: alerts(settings),
            CronTrigger.from_crontab("30 18 * * mon-fri", timezone=TZ),
        )
    return out


def build_scheduler(settings: Settings) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=UTC)
    for job_id, (func, trigger) in jobs(settings).items():
        scheduler.add_job(
            _guard(job_id, func), trigger, id=job_id, coalesce=True, misfire_grace_time=3600
        )
    return scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qp-workers")
    parser.add_argument("--once", action="store_true", help="run jobs once and exit")
    parser.add_argument(
        "--job", action="append", help="with --once: only this job (repeatable; default all)"
    )
    args = parser.parse_args(argv)
    settings = init_platform()
    if args.once:
        selected = jobs(settings)
        unknown = set(args.job or ()) - set(selected)
        if unknown:
            parser.error(f"unknown or unscheduled job(s): {sorted(unknown)}")
        for job_id, (func, _) in selected.items():
            if args.job and job_id not in args.job:
                continue
            log.info("workers.run_once", job=job_id)
            _guard(job_id, func)()
        return 0
    scheduler = build_scheduler(settings)
    log.info("workers.start", jobs=[j.id for j in scheduler.get_jobs()])
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("workers.stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
