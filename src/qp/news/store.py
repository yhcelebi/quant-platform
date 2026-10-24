"""Store feed items once (ADR 0012, 7 and 9) with their entities and tags; query them as
known at ``as_of``."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from qp.core import ensure_utc
from qp.news.entities import Entity, Pattern, link
from qp.news.models import ItemEntityRow, ItemRow, ItemTagRow, SourceRow
from qp.news.parse import FeedItem
from qp.news.tags import TagRule, tag
from qp.news.text import canonical_url, normalized_title
from qp.platform.db import Database

DUPLICATE_WINDOW = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class SourceInfo:
    code: str
    name: str
    url: str
    language: str
    public_institution: bool


def ensure_sources(db: Database, sources: Sequence[SourceInfo]) -> None:
    with db.session() as s:
        for src in sources:
            row = s.get(SourceRow, src.code)
            if row is None:
                s.add(
                    SourceRow(
                        code=src.code,
                        name=src.name,
                        url=src.url,
                        language=src.language,
                        public_institution=src.public_institution,
                    )
                )
            else:
                row.name, row.url, row.language = src.name, src.url, src.language
                row.public_institution = src.public_institution


@dataclass
class StoreResult:
    new_ids: list[int] = field(default_factory=list)
    duplicates: int = 0


def store_items(
    db: Database,
    source: str,
    items: Sequence[FeedItem],
    *,
    fetched_at: datetime,
    raw_ref: str,
    patterns: Sequence[Pattern],
    rules: Sequence[TagRule],
    institution: str = "",
) -> StoreResult:
    """Insert new items; skip known URLs and same-source titles within 48 hours."""
    fetched_at = ensure_utc(fetched_at)
    result = StoreResult()
    with db.session() as s:
        for item in sorted(items, key=lambda i: i.published_at):
            url = canonical_url(item.link)
            norm = normalized_title(item.title)
            if s.scalar(select(ItemRow.id).where(ItemRow.url == url)) is not None:
                result.duplicates += 1
                continue
            near = s.scalars(
                select(ItemRow.published_at).where(
                    ItemRow.source_code == source, ItemRow.title_norm == norm
                )
            ).all()
            if any(abs(p - item.published_at) <= DUPLICATE_WINDOW for p in near):
                result.duplicates += 1
                continue
            row = ItemRow(
                source_code=source,
                guid=item.guid,
                url=url,
                title=item.title,
                title_norm=norm,
                summary=item.summary,
                categories="|".join(item.categories),
                published_at=item.published_at,
                fetched_at=fetched_at,
                known_time=max(item.published_at, fetched_at),
                raw_ref=raw_ref,
            )
            s.add(row)
            s.flush()
            text = f"{item.title} {item.summary}"
            entities = link(text, patterns)
            if institution and not any(e.key == institution for e in entities):
                entities.append(Entity("institution", institution, f"source:{source}"))
            for e in entities:
                s.add(ItemEntityRow(item_id=row.id, kind=e.kind, key=e.key, matched=e.matched))
            for t in tag(text, rules):
                s.add(ItemTagRow(item_id=row.id, tag=t))
            result.new_ids.append(row.id)
    return result


@dataclass(frozen=True, slots=True)
class NewsItem:
    id: int
    source: str
    title: str
    url: str
    summary: str
    published_at: datetime
    known_time: datetime
    entities: tuple[Entity, ...]
    tags: tuple[str, ...]


def query(
    db: Database,
    *,
    as_of: datetime,
    since: datetime | None = None,
    sources: Sequence[str] | None = None,
    tag_filter: str | None = None,
    entity_keys: Sequence[str] | None = None,
    limit: int = 200,
) -> list[NewsItem]:
    """Items known at ``as_of`` (newest first), optionally filtered."""
    as_of = ensure_utc(as_of)
    with db.session() as s:
        stmt = select(ItemRow).where(ItemRow.known_time <= as_of)
        if since is not None:
            stmt = stmt.where(ItemRow.published_at >= ensure_utc(since))
        if sources:
            stmt = stmt.where(ItemRow.source_code.in_(list(sources)))
        if tag_filter:
            stmt = stmt.where(
                ItemRow.id.in_(select(ItemTagRow.item_id).where(ItemTagRow.tag == tag_filter))
            )
        if entity_keys:
            stmt = stmt.where(
                ItemRow.id.in_(
                    select(ItemEntityRow.item_id).where(ItemEntityRow.key.in_(list(entity_keys)))
                )
            )
        rows = s.scalars(stmt.order_by(ItemRow.published_at.desc()).limit(limit)).all()
        ids = [r.id for r in rows]
        ents: dict[int, list[Entity]] = defaultdict(list)
        for e in s.scalars(select(ItemEntityRow).where(ItemEntityRow.item_id.in_(ids))):
            ents[e.item_id].append(Entity(e.kind, e.key, e.matched))
        tags: dict[int, list[str]] = defaultdict(list)
        for t in s.scalars(select(ItemTagRow).where(ItemTagRow.item_id.in_(ids))):
            tags[t.item_id].append(t.tag)
        return [
            NewsItem(
                r.id,
                r.source_code,
                r.title,
                r.url,
                r.summary,
                r.published_at,
                r.known_time,
                tuple(ents[r.id]),
                tuple(sorted(tags[r.id])),
            )
            for r in rows
        ]
