"""Trader entry point: ``make trader-paper`` (python -m apps.trader.main --mode paper).

Runs one daily paper cycle for every strategy in paper status (ADR 0015): risk gate, kill
switch, fills, ledger-bridge reconciliation, heartbeat. Normally scheduled by the workers
after the evening data jobs. There is no live path (phase 8, owner approval required).
"""

from __future__ import annotations

import argparse

from qp.core import SystemClock
from qp.execution.service import run_paper
from qp.platform import Database, Mode, get_logger, init_platform, live_trading_env_acknowledged

log = get_logger("apps.trader")

EXIT_REFUSED = 2
EXIT_NO_DB = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qp-trader")
    parser.add_argument("--mode", default="paper", choices=[m.value for m in Mode])
    args = parser.parse_args(argv)
    settings = init_platform(mode=args.mode)
    if settings.mode is not Mode.PAPER:
        log.error("trader.refused", mode=str(settings.mode), reason="only paper mode exists")
        return EXIT_REFUSED
    if live_trading_env_acknowledged():
        log.warning("trader.live_ack_ignored", reason="QP_LIVE_TRADING is set but mode is paper")
    try:
        with Database.from_settings(settings.db) as db:
            reports = run_paper(settings, db, now=SystemClock().now())
    except Exception as exc:  # no database: say so, never pretend a cycle ran
        log.error("trader.no_database", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return EXIT_NO_DB
    for r in reports:
        log.info(
            "trader.cycle",
            strategy=r.strategy,
            bars=[str(d) for d in r.processed],
            orders=r.orders,
            rejected=len(r.rejected),
            reconciled=r.reconciled,
            halted=r.halted,
        )
    log.info("trader.done", strategies=len(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
