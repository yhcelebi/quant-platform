"""Trader entry point: ``make trader-paper`` (python -m apps.trader.main --mode paper).

Phase 0 stub. It runs in paper mode only and does nothing yet; the TradingNode, risk gate,
kill switch, heartbeat and reconciliation arrive in phase 7. No live path exists.
"""

from __future__ import annotations

import argparse

from qp.platform import Mode, get_logger, init_platform, live_trading_env_acknowledged

log = get_logger("apps.trader")

EXIT_REFUSED = 2


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
    log.info("trader.not_implemented", mode=str(settings.mode), phase=7)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
