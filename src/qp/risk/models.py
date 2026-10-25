"""``trading`` schema (ADR 0015): strategy deployments, promotions, orders, fills, daily
position snapshots, reconciliations, kill-switch state and heartbeats.

Here, not in qp.execution or qp.strategies, because the pre-trade gate (qp.risk) reads them
and sits below both. Decision records are append-only (database triggers, migration 0007).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from qp.platform.db import Base

STATUSES = ("research", "backtested", "paper", "approved_live", "retired")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class StrategyRow(Base):
    __tablename__ = "strategies"
    __table_args__ = (
        CheckConstraint("name ~ '^[a-z0-9][a-z0-9_-]*$'", name="name_format"),
        CheckConstraint("(instrument IS NULL) <> (fx IS NULL)", name="one_market"),
        {"schema": "trading"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    signal: Mapped[str] = mapped_column(Text)
    params: Mapped[str] = mapped_column(Text, server_default="{}")  # JSON
    instrument: Mapped[str | None] = mapped_column(Text)
    fx: Mapped[str | None] = mapped_column(Text)
    costs: Mapped[str] = mapped_column(Text)
    capital: Mapped[Decimal] = mapped_column(Numeric)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromotionRow(Base):
    __tablename__ = "promotions"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="status"),
        CheckConstraint("status <> 'approved_live' OR owner_confirmed", name="live_needs_owner"),
        {"schema": "trading"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("trading.strategies.id"))
    status: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text, server_default="")
    note: Mapped[str] = mapped_column(Text, server_default="")
    owner_confirmed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = ({"schema": "trading"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("trading.strategies.id"))
    bar_date: Mapped[date] = mapped_column(Date)
    target: Mapped[float] = mapped_column(Float)
    quantity: Mapped[Decimal] = mapped_column(Numeric)  # signed
    price: Mapped[Decimal] = mapped_column(Numeric)
    accepted: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FillRow(Base):
    __tablename__ = "fills"
    __table_args__ = ({"schema": "trading"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("trading.orders.id"), unique=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("trading.strategies.id"))
    bar_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric)
    price: Mapped[Decimal] = mapped_column(Numeric)
    fee: Mapped[Decimal] = mapped_column(Numeric)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SnapshotRow(Base):
    __tablename__ = "positions_snapshot"
    __table_args__ = (
        UniqueConstraint("strategy_id", "bar_date", name="uq_positions_snapshot_day"),
        {"schema": "trading"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("trading.strategies.id"))
    bar_date: Mapped[date] = mapped_column(Date)
    close: Mapped[Decimal] = mapped_column(Numeric)
    quantity: Mapped[Decimal] = mapped_column(Numeric)
    cash: Mapped[Decimal] = mapped_column(Numeric)
    equity: Mapped[Decimal] = mapped_column(Numeric)
    pending_target: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReconciliationRow(Base):
    __tablename__ = "reconciliations"
    __table_args__ = ({"schema": "trading"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("trading.strategies.id"))
    bar_date: Mapped[date] = mapped_column(Date)
    ok: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TradingStateRow(Base):
    __tablename__ = "trading_state"
    __table_args__ = ({"schema": "trading"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope: Mapped[str] = mapped_column(Text)  # "global" or a strategy name
    halted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HeartbeatRow(Base):
    __tablename__ = "heartbeats"
    __table_args__ = ({"schema": "trading"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    component: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
