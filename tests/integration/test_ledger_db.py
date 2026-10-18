"""Ledger against a real PostgreSQL 16: append-only guarantees and the full import flow.

The import flow uses the documented example files (docs/examples/), which reproduce
reference cases a-d with dates, so the documentation is tested too.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from qp.core import TRY, USD, InstrumentId, SystemClock
from qp.data.manual import ManualMarketData
from qp.ledger.domain import Account, AccountPurpose, AccountType, LedgerError, TxType
from qp.ledger.engine import replay
from qp.ledger.importer import commit_import, plan_import_from_db
from qp.ledger.repo import LedgerRepository
from qp.ledger.service import add_account, reverse
from qp.platform import Database
from qp.portfolio.holdings import build_report
from qp.portfolio.reconcile import reconcile
from qp.reference.importer import plan_instrument_import
from qp.reference.repo import ReferenceRepository

pytestmark = pytest.mark.integration

EXAMPLES = Path(__file__).resolve().parents[2] / "docs" / "examples"
THYAO = InstrumentId.parse("THYAO.BIST")
AAPL = InstrumentId.parse("AAPL.NASDAQ")


def _setup(db: Database) -> None:
    with db.session() as s:
        ref = ReferenceRepository(s)
        plan = plan_instrument_import(
            (EXAMPLES / "instruments.csv").read_text(), {}, {v.code for v in ref.venues()}
        )
        assert plan.ok, plan.errors
        for inst in plan.new:
            ref.add_instrument(inst)
        add_account(
            s, Account("EX", "Example", AccountType.BROKERAGE, AccountPurpose.LONG_TERM, TRY)
        )


def _import(db: Database, content: bytes, name: str = "ledger.csv") -> list[str]:
    with db.session() as s:
        plan = plan_import_from_db(s, content, name, now=SystemClock().now())
        if plan.ok:
            commit_import(s, plan)
        return plan.errors


def test_example_files_reproduce_reference_cases(migrated_db: Database) -> None:
    _setup(migrated_db)
    assert _import(migrated_db, (EXAMPLES / "ledger.csv").read_bytes()) == []
    with migrated_db.session() as s:
        txs = LedgerRepository(s).transactions()
    state = replay(txs)

    thyao = state.position("EX", THYAO)
    assert thyao is not None
    assert (thyao.quantity, thyao.cost, thyao.avg_cost) == (
        Decimal(300),
        Decimal("2252.25"),
        Decimal("7.5075"),
    )
    sale = state.realizations[0]
    assert (sale.proceeds, sale.cost, sale.realized) == (
        Decimal("1498.50"),
        Decimal("750.75"),
        Decimal("747.75"),
    )
    div = state.income[0]
    assert (div.gross, div.withholding, div.net) == (
        Decimal("150.00"),
        Decimal("15.00"),
        Decimal("135.00"),
    )
    aapl = state.position("EX", AAPL)
    assert aapl is not None
    assert (aapl.cost, aapl.cost_try) == (Decimal("2001.00"), Decimal("80040.00"))
    assert state.cash[("EX", TRY)] == Decimal("8630.50")
    assert state.cash[("EX", USD)] == 0

    market = ManualMarketData.from_dir(EXAMPLES)
    report = build_report(txs, market, as_of=date(2025, 1, 14))
    row = next(r for r in report.positions if r.valuation.instrument == AAPL)
    v = row.valuation
    assert (v.value_try, v.unrealized_try) == (Decimal("92400.00"), Decimal("12360.00"))
    assert (v.fx_effect_try, v.price_effect_try) == (Decimal("4002.00"), Decimal("8358.00"))
    assert report.complete

    assert reconcile(state, (EXAMPLES / "statement.csv").read_text()) == []


def test_reimport_and_duplicate_refs_are_rejected(migrated_db: Database) -> None:
    _setup(migrated_db)
    content = (EXAMPLES / "ledger.csv").read_bytes()
    assert _import(migrated_db, content) == []
    assert any("already imported" in e for e in _import(migrated_db, content))
    dup = (
        b"account,type,trade_date,currency,amount,external_ref\nEX,deposit,2025-02-01,TRY,1,dep-1\n"
    )
    assert any(
        "external_ref 'dep-1' already used" in e for e in _import(migrated_db, dup, "dup.csv")
    )


def test_invalid_file_writes_nothing(migrated_db: Database) -> None:
    _setup(migrated_db)
    bad = (
        b"account,type,trade_date,instrument,quantity,price,currency\n"
        b"EX,buy,2025-01-03,THYAO.BIST,10,1,TRY\n"
        b"EX,sell,2025-01-04,THYAO.BIST,11,1,TRY\n"  # oversell -> whole file rejected
    )
    errors = _import(migrated_db, bad, "bad.csv")
    assert any("only 10 held" in e for e in errors)
    with migrated_db.session() as s:
        assert LedgerRepository(s).transactions() == []


def test_append_only_triggers(migrated_db: Database) -> None:
    _setup(migrated_db)
    _import(migrated_db, (EXAMPLES / "ledger.csv").read_bytes())
    for sql in (
        "UPDATE ledger.transactions SET note = 'x'",
        "DELETE FROM ledger.transaction_legs",
        "TRUNCATE ledger.import_batches CASCADE",
        "UPDATE ref.corporate_actions SET note = 'x'",
    ):
        with pytest.raises(DBAPIError, match="append-only"), migrated_db.engine.begin() as conn:
            conn.execute(text(sql))


def test_recorded_at_cannot_be_backdated(migrated_db: Database) -> None:
    _setup(migrated_db)
    with migrated_db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ledger.transactions (account_code, tx_type, trade_date, recorded_at) "
                "VALUES ('EX', 'deposit', '2025-01-01', '2000-01-01T00:00:00Z')"
            )
        )
        recorded = conn.execute(text("SELECT recorded_at FROM ledger.transactions")).scalar_one()
    assert recorded > datetime.now(UTC) - timedelta(minutes=5)


def test_reversal_keeps_history_and_known_at_view(migrated_db: Database) -> None:
    _setup(migrated_db)
    _import(migrated_db, (EXAMPLES / "ledger.csv").read_bytes())
    with migrated_db.session() as s:
        txs = LedgerRepository(s).transactions()
    buy = next(t for t in txs if t.tx_type is TxType.BUY and t.external_ref == "t-4")  # AAPL
    with migrated_db.engine.connect() as conn:  # the database clock stamps recorded_at
        before_reversal = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    with migrated_db.session() as s:
        rev = reverse(s, buy.id or 0, note="test: entered by mistake", now=SystemClock().now())
    with migrated_db.session() as s:
        txs = LedgerRepository(s).transactions()
    assert any(t.id == buy.id for t in txs)  # the original row is kept
    now_state = replay(txs)
    old_view = replay(txs, known_at=before_reversal).position("EX", AAPL)
    assert now_state.position("EX", AAPL) is None
    assert old_view is not None
    assert old_view.quantity == 10
    assert now_state.cash[("EX", USD)] == Decimal("2001")
    with migrated_db.session() as s, pytest.raises(LedgerError, match="already reversed"):
        reverse(s, buy.id or 0, note="again", now=SystemClock().now())
    with migrated_db.session() as s, pytest.raises(LedgerError, match="cannot be reversed"):
        reverse(s, rev.id or 0, note="undo", now=SystemClock().now())


def test_reversal_that_breaks_the_ledger_is_refused(migrated_db: Database) -> None:
    _setup(migrated_db)
    _import(migrated_db, (EXAMPLES / "ledger.csv").read_bytes())
    with migrated_db.session() as s:
        first_buy = next(t for t in LedgerRepository(s).transactions() if t.tx_type is TxType.BUY)
    with migrated_db.session() as s, pytest.raises(LedgerError):
        reverse(
            s,
            first_buy.id or 0,
            note="would make later sells and the bonus inconsistent",
            now=SystemClock().now(),
        )
