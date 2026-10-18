"""Workers entry point: ``make workers`` (python -m apps.workers.main).

Phase 0 registers only a heartbeat job. Ingestion, quality checks, derived data, portfolio
snapshots and alert evaluation are added in later phases; job logic lives in qp.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC

from apscheduler.schedulers.blocking import BlockingScheduler

from qp.platform import Settings, get_logger, init_platform

log = get_logger("apps.workers")


def heartbeat(settings: Settings) -> None:
    log.info("workers.heartbeat", mode=str(settings.mode))


def jobs(settings: Settings) -> dict[str, tuple[Callable[[], None], int]]:
    """Job id -> (callable, interval in seconds)."""
    return {"heartbeat": (lambda: heartbeat(settings), 60)}


def build_scheduler(settings: Settings) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=UTC)
    for job_id, (func, seconds) in jobs(settings).items():
        scheduler.add_job(func, "interval", seconds=seconds, id=job_id, coalesce=True)
    return scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qp-workers")
    parser.add_argument("--once", action="store_true", help="run every job once and exit")
    args = parser.parse_args(argv)
    settings = init_platform()
    if args.once:
        for job_id, (func, _) in jobs(settings).items():
            log.info("workers.run_once", job=job_id)
            func()
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
