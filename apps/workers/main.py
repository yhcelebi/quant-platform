"""Workers entry point: ``make workers`` (python -m apps.workers.main).

Jobs (Europe/Istanbul times; job logic lives in qp, every run is recorded in ops.job_runs):

- heartbeat      every 60 s
- tcmb_fx        Mon-Fri 15:45 and 18:15: fetch new TCMB bulletins, then quality checks
- fred           daily 07:00, only with QP_SOURCES__FRED__API_KEY
- evds           daily 17:30, only with QP_SOURCES__EVDS__API_KEY and configured series

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

from qp.data import service
from qp.platform import Database, Settings, get_logger, init_platform

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
