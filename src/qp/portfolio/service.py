"""Wiring for apps: load the ledger from the database and market data from settings."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime

from qp.data.lake import CleanStore
from qp.data.lake_market import LakeMarketData
from qp.data.manual import ManualMarketData
from qp.data.market import LayeredMarketData
from qp.ledger.domain import Account
from qp.ledger.engine import replay
from qp.ledger.repo import LedgerRepository
from qp.platform.db import Database
from qp.platform.lake import LakePaths
from qp.platform.settings import Settings
from qp.portfolio.holdings import CostMethod, HoldingsReport, build_report
from qp.portfolio.reconcile import Mismatch, reconcile


def market_data(settings: Settings) -> LayeredMarketData:
    """Owner-maintained manual values first, then the lake (newest date wins; ties: manual)."""
    lake = LakeMarketData(
        CleanStore(LakePaths(settings.lake.root)), rate_kind=settings.sources.tcmb.rate_kind
    )
    return LayeredMarketData([ManualMarketData.from_dir(settings.manual.dir), lake])


def load_report(
    settings: Settings,
    *,
    as_of: date,
    known_at: datetime | None = None,
    accounts: Collection[str] | None = None,
    method: CostMethod = CostMethod.AVERAGE,
    stale_after_days: int = 7,
) -> tuple[HoldingsReport, list[Account]]:
    market = market_data(settings)
    db = Database.from_settings(settings.db)
    try:
        with db.session() as session:
            repo = LedgerRepository(session)
            txs = repo.transactions()
            all_accounts = repo.accounts()
    finally:
        db.dispose()
    report = build_report(
        txs,
        market,
        as_of=as_of,
        known_at=known_at,
        accounts=accounts,
        method=method,
        stale_after_days=stale_after_days,
    )
    return report, all_accounts


def reconcile_statement(
    settings: Settings, statement_text: str, *, as_of: date, include_cash: bool = True
) -> list[Mismatch]:
    db = Database.from_settings(settings.db)
    try:
        with db.session() as session:
            txs = LedgerRepository(session).transactions()
    finally:
        db.dispose()
    return reconcile(replay(txs, as_of=as_of), statement_text, include_cash=include_cash)
