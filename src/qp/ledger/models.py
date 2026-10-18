"""ORM models for the ``ledger`` schema (ADR 0005).

``transactions``, ``transaction_legs`` and ``import_batches`` are append-only: database
triggers reject UPDATE, DELETE and TRUNCATE, and force ``recorded_at = now()`` on insert so
entries cannot be backdated.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from qp.platform.db import Base

ACCOUNT_TYPES = ("brokerage", "bank", "crypto_exchange", "pension", "other")
PURPOSES = ("long_term", "trading")
TX_TYPES = (
    "opening_balance",
    "deposit",
    "withdrawal",
    "buy",
    "sell",
    "fee",
    "interest",
    "tax",
    "fx_conversion",
    "transfer_out",
    "transfer_in",
    "split",
    "bonus_issue",
    "stock_dividend",
    "rights_subscription",
    "cash_dividend",
    "merger",
    "delisting",
    "reversal",
)
LEG_KINDS = ("position", "cash", "fee", "tax", "income")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AccountRow(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Za-z0-9][A-Za-z0-9_.-]*$'", name="code_format"),
        CheckConstraint(_in("account_type", ACCOUNT_TYPES), name="account_type"),
        CheckConstraint(_in("purpose", PURPOSES), name="purpose"),
        {"schema": "ledger"},
    )

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    account_type: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    base_currency: Mapped[str] = mapped_column(String(3))
    broker: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SleeveRow(Base):
    __tablename__ = "sleeves"
    __table_args__ = (
        CheckConstraint(_in("purpose", PURPOSES), name="purpose"),
        {"schema": "ledger"},
    )

    account_code: Mapped[str] = mapped_column(ForeignKey("ledger.accounts.code"), primary_key=True)
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, server_default="")
    purpose: Mapped[str] = mapped_column(Text)


class ImportBatchRow(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint("file_sha256"),
        CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        {"schema": "ledger"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_name: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str] = mapped_column(Text, server_default="")


class TransactionRow(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_code", "external_ref"),
        UniqueConstraint("reverses_id"),
        CheckConstraint(_in("tx_type", TX_TYPES), name="tx_type"),
        CheckConstraint("fx_rate_try IS NULL OR fx_rate_try > 0", name="fx_positive"),
        CheckConstraint("(tx_type = 'reversal') = (reverses_id IS NOT NULL)", name="reversal_link"),
        CheckConstraint(
            "(tx_type IN ('transfer_in', 'transfer_out')) = (transfer_id IS NOT NULL)",
            name="transfer_link",
        ),
        ForeignKeyConstraint(
            ["account_code", "sleeve_code"], ["ledger.sleeves.account_code", "ledger.sleeves.code"]
        ),
        {"schema": "ledger"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_code: Mapped[str] = mapped_column(ForeignKey("ledger.accounts.code"))
    sleeve_code: Mapped[str] = mapped_column(Text, server_default="main")
    tx_type: Mapped[str] = mapped_column(Text)
    trade_date: Mapped[date] = mapped_column(Date)
    settle_date: Mapped[date | None] = mapped_column(Date)
    trade_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(Integer, server_default="0")
    fx_rate_try: Mapped[Decimal | None] = mapped_column(Numeric)
    corporate_action_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ref.corporate_actions.id")
    )
    transfer_id: Mapped[str | None] = mapped_column(Text)
    reverses_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ledger.transactions.id")
    )
    external_ref: Mapped[str | None] = mapped_column(Text)
    import_batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ledger.import_batches.id")
    )
    note: Mapped[str] = mapped_column(Text, server_default="")
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LegRow(Base):
    __tablename__ = "transaction_legs"
    __table_args__ = (
        UniqueConstraint("transaction_id", "leg_no"),
        CheckConstraint(_in("kind", LEG_KINDS), name="kind"),
        CheckConstraint(
            "(kind = 'position') = (instrument_id IS NOT NULL AND quantity IS NOT NULL)",
            name="position_fields",
        ),
        CheckConstraint("kind = 'cash' OR amount >= 0", name="non_cash_non_negative"),
        {"schema": "ledger"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ledger.transactions.id"))
    leg_no: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric)
    instrument_id: Mapped[str | None] = mapped_column(ForeignKey("ref.instruments.id"))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric)
