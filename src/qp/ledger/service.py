"""Ledger write operations that need validation across the whole ledger."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from qp.core import istanbul_date
from qp.ledger import builders as b
from qp.ledger.domain import Account, LedgerError, Transaction, TxType
from qp.ledger.engine import replay
from qp.ledger.repo import LedgerRepository


def add_account(session: Session, account: Account) -> None:
    repo = LedgerRepository(session)
    if repo.account(account.code) is not None:
        raise LedgerError(f"account {account.code} already exists")
    repo.add_account(account)


def reverse(session: Session, tx_id: int, *, note: str, now: datetime) -> Transaction:
    """Append a reversal that cancels ``tx_id`` (rule 13), after checking that the ledger
    stays consistent without it (e.g. no later sale would oversell)."""
    if not note.strip():
        raise LedgerError("a reversal needs a note explaining why")
    repo = LedgerRepository(session)
    txs = repo.transactions()
    target = next((t for t in txs if t.id == tx_id), None)
    if target is None:
        raise LedgerError(f"transaction {tx_id} does not exist")
    if target.tx_type is TxType.REVERSAL:
        raise LedgerError("a reversal cannot be reversed; record the original again instead")
    if any(t.reverses_id == tx_id for t in txs):
        raise LedgerError(f"transaction {tx_id} is already reversed")
    rev = b.reversal(b.Common(target.account, istanbul_date(now), now, note=note), tx_id)
    replay([*txs, rev])  # raises LedgerError if the ledger breaks without the original
    return repo.insert([rev])[0]
