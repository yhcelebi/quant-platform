"""Database access for the ledger. Insert-only for transactions (the database enforces it)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from qp.core import Currency, InstrumentId
from qp.ledger.domain import (
    Account,
    AccountPurpose,
    AccountType,
    LedgerError,
    Leg,
    LegKind,
    Transaction,
    TxType,
)
from qp.ledger.models import AccountRow, ImportBatchRow, LegRow, SleeveRow, TransactionRow
from qp.reference.domain import CorporateAction
from qp.reference.models import CorporateActionRow
from qp.reference.repo import corporate_action_from_row


def _account(row: AccountRow) -> Account:
    return Account(
        code=row.code,
        name=row.name,
        account_type=AccountType(row.account_type),
        purpose=AccountPurpose(row.purpose),
        base_currency=Currency(row.base_currency),
        broker=row.broker,
    )


class LedgerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------- accounts
    def accounts(self) -> list[Account]:
        return [
            _account(r) for r in self.session.scalars(select(AccountRow).order_by(AccountRow.code))
        ]

    def account(self, code: str) -> Account | None:
        row = self.session.get(AccountRow, code)
        return _account(row) if row is not None else None

    def add_account(self, account: Account) -> None:
        """Create an account with its default sleeve ``main``."""
        self.session.add(
            AccountRow(
                code=account.code,
                name=account.name,
                account_type=account.account_type.value,
                purpose=account.purpose.value,
                base_currency=account.base_currency.code,
                broker=account.broker,
            )
        )
        self.session.flush()
        self.session.add(
            SleeveRow(
                account_code=account.code, code="main", name="main", purpose=account.purpose.value
            )
        )
        self.session.flush()

    # ------------------------------------------------------------- reads
    def batch_exists(self, sha256: str) -> bool:
        stmt = select(ImportBatchRow.id).where(ImportBatchRow.file_sha256 == sha256)
        return self.session.scalars(stmt).first() is not None

    def transactions(self) -> list[Transaction]:
        """Every ledger transaction (all accounts: transfers pair across accounts)."""
        tx_rows = list(self.session.scalars(select(TransactionRow).order_by(TransactionRow.id)))
        legs: dict[int, list[LegRow]] = defaultdict(list)
        for leg in self.session.scalars(
            select(LegRow).order_by(LegRow.transaction_id, LegRow.leg_no)
        ):
            legs[leg.transaction_id].append(leg)
        ca_ids = {r.corporate_action_id for r in tx_rows if r.corporate_action_id is not None}
        actions: dict[int, CorporateAction] = {}
        if ca_ids:
            stmt = select(CorporateActionRow).where(CorporateActionRow.id.in_(ca_ids))
            actions = {r.id: corporate_action_from_row(r) for r in self.session.scalars(stmt)}
        return [self._to_domain(r, legs[r.id], actions) for r in tx_rows]

    @staticmethod
    def _to_domain(
        row: TransactionRow, legs: list[LegRow], actions: dict[int, CorporateAction]
    ) -> Transaction:
        ca = actions.get(row.corporate_action_id) if row.corporate_action_id is not None else None
        return Transaction(
            id=row.id,
            account=row.account_code,
            sleeve=row.sleeve_code,
            tx_type=TxType(row.tx_type),
            trade_date=row.trade_date,
            settle_date=row.settle_date,
            trade_time=row.trade_time,
            sequence=row.sequence,
            recorded_at=row.recorded_at,
            fx_rate_try=row.fx_rate_try,
            corporate_action=ca,
            transfer_id=row.transfer_id,
            reverses_id=row.reverses_id,
            external_ref=row.external_ref,
            note=row.note,
            legs=tuple(
                Leg(
                    kind=LegKind(leg.kind),
                    currency=Currency(leg.currency),
                    amount=leg.amount,
                    instrument=InstrumentId.parse(leg.instrument_id) if leg.instrument_id else None,
                    quantity=leg.quantity,
                )
                for leg in legs
            ),
        )

    def external_refs(self) -> set[tuple[str, str]]:
        stmt = select(TransactionRow.account_code, TransactionRow.external_ref).where(
            TransactionRow.external_ref.is_not(None)
        )
        return {(a, r) for a, r in self.session.execute(stmt) if r is not None}

    # ------------------------------------------------------------- writes
    def add_batch(self, file_name: str, sha256: str, row_count: int, note: str = "") -> int:
        row = ImportBatchRow(
            file_name=file_name, file_sha256=sha256, row_count=row_count, note=note
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def insert(
        self, transactions: Sequence[Transaction], *, batch_id: int | None = None
    ) -> list[Transaction]:
        """Insert transactions; returns them with database ids and ``recorded_at``.

        Linked corporate actions must already have ids (insert them first)."""
        stored: list[Transaction] = []
        for tx in transactions:
            ca = tx.corporate_action
            if ca is not None and ca.id is None:
                raise LedgerError(f"{tx.describe()}: corporate action must be stored first")
            row = TransactionRow(
                account_code=tx.account,
                sleeve_code=tx.sleeve,
                tx_type=tx.tx_type.value,
                trade_date=tx.trade_date,
                settle_date=tx.settle_date,
                trade_time=tx.trade_time,
                sequence=tx.sequence,
                fx_rate_try=tx.fx_rate_try,
                corporate_action_id=ca.id if ca is not None else None,
                transfer_id=tx.transfer_id,
                reverses_id=tx.reverses_id,
                external_ref=tx.external_ref,
                import_batch_id=batch_id,
                note=tx.note,
            )
            self.session.add(row)
            self.session.flush()
            for no, leg in enumerate(tx.legs):
                self.session.add(
                    LegRow(
                        transaction_id=row.id,
                        leg_no=no,
                        kind=leg.kind.value,
                        currency=leg.currency.code,
                        amount=leg.amount,
                        instrument_id=str(leg.instrument) if leg.instrument else None,
                        quantity=leg.quantity,
                    )
                )
            self.session.flush()
            self.session.refresh(row)  # recorded_at is set by the database
            stored.append(replace(tx, id=row.id, recorded_at=row.recorded_at))
        return stored
