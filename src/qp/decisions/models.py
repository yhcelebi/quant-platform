"""``journal`` schema (ADR 0013, 13): append-only theses, reviews, entries, watchlist events.

Rows are never updated or deleted (database triggers, migration 0006); ``created_at`` is
stamped by the database, so nothing can be backdated. A thesis's status and next review
date come from its latest review.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from qp.platform.db import Base

DIRECTIONS = ("long", "short", "neutral", "macro")
VERDICTS = ("keep", "close", "invalidated")
ENTRY_KINDS = ("note", "decision")
WATCH_ACTIONS = ("add", "remove")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ThesisRow(Base):
    __tablename__ = "theses"
    __table_args__ = (
        CheckConstraint(_in("direction", DIRECTIONS), name="direction"),
        CheckConstraint("length(invalidation) > 0", name="has_invalidation"),
        {"schema": "journal"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    instrument_id: Mapped[str | None] = mapped_column(ForeignKey("ref.instruments.id"))
    direction: Mapped[str] = mapped_column(Text)
    thesis: Mapped[str] = mapped_column(Text)
    invalidation: Mapped[str] = mapped_column(Text)
    first_review: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewRow(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint(_in("verdict", VERDICTS), name="verdict"),
        CheckConstraint("(verdict = 'keep') = (next_review IS NOT NULL)", name="keep_has_next"),
        {"schema": "journal"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("journal.theses.id"))
    verdict: Mapped[str] = mapped_column(Text)
    next_review: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntryRow(Base):
    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint(_in("kind", ENTRY_KINDS), name="kind"),
        {"schema": "journal"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thesis_id: Mapped[int | None] = mapped_column(ForeignKey("journal.theses.id"))
    kind: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WatchlistEventRow(Base):
    __tablename__ = "watchlists"
    __table_args__ = (
        CheckConstraint(_in("action", WATCH_ACTIONS), name="action"),
        {"schema": "journal"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("ref.instruments.id"))
    action: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
