"""``ops.alert_events``: each alert stored once per (rule, subject, key date), then sent."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from qp.platform.db import Base

ALERT_LEVELS = ("info", "warning", "critical")


class AlertEventRow(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        CheckConstraint(f"level IN ({', '.join(repr(s) for s in ALERT_LEVELS)})", name="level"),
        UniqueConstraint("rule", "subject", "key_date", name="uq_alert_events_key"),
        {"schema": "ops"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rule: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    key_date: Mapped[date] = mapped_column(Date)
    level: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_error: Mapped[str] = mapped_column(Text, server_default="")
