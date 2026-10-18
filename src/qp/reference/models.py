"""ORM models for the ``ref`` schema. DDL lives in Alembic (db/migrations); a test checks
that these models and the migrations agree."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from qp.platform.db import Base

ASSET_CLASSES = ("equity", "etf", "fund", "bond", "crypto", "commodity", "other")
ACTION_TYPES = (
    "split",
    "bonus_issue",
    "stock_dividend",
    "rights_issue",
    "cash_dividend",
    "merger",
    "delisting",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class VenueRow(Base):
    __tablename__ = "venues"
    __table_args__ = ({"schema": "ref"},)

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    currency: Mapped[str | None] = mapped_column(String(3))


class InstrumentRow(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint("id = symbol || '.' || venue_code", name="id_is_symbol_venue"),
        CheckConstraint(_in("asset_class", ASSET_CLASSES), name="asset_class"),
        {"schema": "ref"},
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text)
    venue_code: Mapped[str] = mapped_column(ForeignKey("ref.venues.code"))
    asset_class: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(Text, server_default="")
    isin: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstrumentAliasRow(Base):
    __tablename__ = "instrument_aliases"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_symbol", "valid_from", postgresql_nulls_not_distinct=True
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name="valid_range"
        ),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    provider_symbol: Mapped[str] = mapped_column(Text)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("ref.instruments.id"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class CorporateActionRow(Base):
    """Append-only (database trigger). Parameters per type: see qp.reference.domain."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("instrument_id", "action_type", "ex_date"),
        CheckConstraint(_in("action_type", ACTION_TYPES), name="action_type"),
        CheckConstraint("ratio IS NULL OR ratio > 0", name="ratio_positive"),
        CheckConstraint("amount_per_share IS NULL OR amount_per_share > 0", name="amount_positive"),
        CheckConstraint("pay_date IS NULL OR pay_date >= ex_date", name="pay_after_ex"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("ref.instruments.id"))
    action_type: Mapped[str] = mapped_column(Text)
    ex_date: Mapped[date] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    pay_date: Mapped[date | None] = mapped_column(Date)
    ratio: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_per_share: Mapped[Decimal | None] = mapped_column(Numeric)
    currency: Mapped[str | None] = mapped_column(String(3))
    subscription_price: Mapped[Decimal | None] = mapped_column(Numeric)
    new_instrument_id: Mapped[str | None] = mapped_column(ForeignKey("ref.instruments.id"))
    source: Mapped[str] = mapped_column(Text)
    known_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str] = mapped_column(Text, server_default="")
