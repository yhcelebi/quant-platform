"""``ops.data_quality_results``: one row per check per run."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from qp.platform.db import Base

QUALITY_STATUSES = ("ok", "warn", "fail")


class DataQualityResultRow(Base):
    __tablename__ = "data_quality_results"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in QUALITY_STATUSES)})", name="status"
        ),
        {"schema": "ops"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("ops.job_runs.id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dataset: Mapped[str] = mapped_column(Text)
    check_name: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text, server_default="")
    status: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, server_default="")
