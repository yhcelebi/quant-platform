"""``news`` schema: sources, items (metadata only), entity links and tags (ADR 0012)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from qp.platform.db import Base


class SourceRow(Base):
    __tablename__ = "sources"
    __table_args__ = ({"schema": "news"},)

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, server_default="")
    public_institution: Mapped[bool] = mapped_column(Boolean, server_default="false")


class ItemRow(Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("known_time >= published_at AND known_time >= fetched_at", name="known"),
        CheckConstraint("length(summary) <= 500", name="summary_short"),
        Index("ix_items_source_title", "source_code", "title_norm"),
        Index("ix_items_known_time", "known_time"),
        {"schema": "news"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_code: Mapped[str] = mapped_column(ForeignKey("news.sources.code"))
    guid: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, server_default="")
    categories: Mapped[str] = mapped_column(Text, server_default="")  # "|"-separated
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    known_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_ref: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemEntityRow(Base):
    __tablename__ = "item_entities"
    __table_args__ = (
        CheckConstraint("kind IN ('instrument', 'currency', 'institution')", name="kind"),
        {"schema": "news"},
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("news.items.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    matched: Mapped[str] = mapped_column(Text)


class ItemTagRow(Base):
    __tablename__ = "item_tags"
    __table_args__ = ({"schema": "news"},)

    item_id: Mapped[int] = mapped_column(ForeignKey("news.items.id"), primary_key=True)
    tag: Mapped[str] = mapped_column(Text, primary_key=True)
