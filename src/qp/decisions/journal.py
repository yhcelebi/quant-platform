"""Decisions journal (ADR 0013, 13): write append-only facts, read derived status."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select

from qp.core import QPValidationError
from qp.decisions.models import (
    DIRECTIONS,
    ENTRY_KINDS,
    VERDICTS,
    EntryRow,
    ReviewRow,
    ThesisRow,
    WatchlistEventRow,
)
from qp.platform.db import Database


def add_thesis(
    db: Database,
    *,
    title: str,
    thesis: str,
    invalidation: str,
    first_review: date,
    direction: str = "long",
    instrument: str | None = None,
) -> int:
    """A thesis must say what would prove it wrong, and when to look again."""
    if direction not in DIRECTIONS:
        raise QPValidationError(f"direction must be one of {DIRECTIONS}")
    if not invalidation.strip():
        raise QPValidationError("a thesis needs invalidation criteria")
    if not title.strip() or not thesis.strip():
        raise QPValidationError("title and thesis are required")
    with db.session() as s:
        row = ThesisRow(
            title=title.strip(),
            instrument_id=instrument,
            direction=direction,
            thesis=thesis.strip(),
            invalidation=invalidation.strip(),
            first_review=first_review,
        )
        s.add(row)
        s.flush()
        return row.id


def review(
    db: Database, thesis_id: int, *, verdict: str, note: str = "", next_review: date | None = None
) -> int:
    if verdict not in VERDICTS:
        raise QPValidationError(f"verdict must be one of {VERDICTS}")
    if (verdict == "keep") != (next_review is not None):
        raise QPValidationError("'keep' needs a next review date; close/invalidated must not")
    with db.session() as s:
        if s.get(ThesisRow, thesis_id) is None:
            raise QPValidationError(f"no thesis {thesis_id}")
        status = _status(s.scalars(select(ReviewRow).where(ReviewRow.thesis_id == thesis_id)).all())
        if status != "open":
            raise QPValidationError(f"thesis {thesis_id} is already {status}")
        row = ReviewRow(thesis_id=thesis_id, verdict=verdict, next_review=next_review, note=note)
        s.add(row)
        s.flush()
        return row.id


def add_entry(db: Database, text: str, *, kind: str = "note", thesis_id: int | None = None) -> int:
    if kind not in ENTRY_KINDS:
        raise QPValidationError(f"kind must be one of {ENTRY_KINDS}")
    if not text.strip():
        raise QPValidationError("empty entry")
    with db.session() as s:
        row = EntryRow(thesis_id=thesis_id, kind=kind, text=text.strip())
        s.add(row)
        s.flush()
        return row.id


def watch(db: Database, name: str, instrument: str, *, remove: bool = False, note: str = "") -> int:
    with db.session() as s:
        row = WatchlistEventRow(
            name=name, instrument_id=instrument, action="remove" if remove else "add", note=note
        )
        s.add(row)
        s.flush()
        return row.id


def watchlists(db: Database) -> dict[str, list[str]]:
    """Current members per list (replaying add/remove events in order)."""
    members: dict[str, dict[str, None]] = defaultdict(dict)
    with db.session() as s:
        for e in s.scalars(select(WatchlistEventRow).order_by(WatchlistEventRow.id)):
            if e.action == "add":
                members[e.name][e.instrument_id] = None
            else:
                members[e.name].pop(e.instrument_id, None)
    return {k: list(v) for k, v in sorted(members.items()) if v}


def _status(reviews: Sequence[ReviewRow]) -> str:
    """open until a review closes or invalidates it; the latest review decides."""
    rows = sorted(reviews, key=lambda r: r.id)
    if not rows:
        return "open"
    last = rows[-1].verdict
    return "open" if last == "keep" else ("closed" if last == "close" else "invalidated")


@dataclass(frozen=True, slots=True)
class ThesisView:
    id: int
    title: str
    instrument: str | None
    direction: str
    thesis: str
    invalidation: str
    created_at: datetime
    status: str  # open | closed | invalidated
    next_review: date | None  # None when not open
    reviews: int
    last_note: str


def theses(db: Database) -> list[ThesisView]:
    with db.session() as s:
        reviews: dict[int, list[ReviewRow]] = defaultdict(list)
        for r in s.scalars(select(ReviewRow).order_by(ReviewRow.id)):
            reviews[r.thesis_id].append(r)
        out: list[ThesisView] = []
        for t in s.scalars(select(ThesisRow).order_by(ThesisRow.id)):
            rs = reviews.get(t.id, [])
            status = _status(rs)
            nxt = (rs[-1].next_review if rs else t.first_review) if status == "open" else None
            out.append(
                ThesisView(
                    t.id,
                    t.title,
                    t.instrument_id,
                    t.direction,
                    t.thesis,
                    t.invalidation,
                    t.created_at,
                    status,
                    nxt,
                    len(rs),
                    rs[-1].note if rs else "",
                )
            )
        return out


def due(db: Database, day: date) -> list[ThesisView]:
    """Open theses whose next review is on or before ``day``."""
    return [t for t in theses(db) if t.status == "open" and t.next_review and t.next_review <= day]


@dataclass(frozen=True, slots=True)
class Entry:
    id: int
    thesis_id: int | None
    kind: str
    text: str
    created_at: datetime


def entries(db: Database, thesis_id: int | None = None, limit: int = 100) -> list[Entry]:
    with db.session() as s:
        stmt = select(EntryRow).order_by(EntryRow.id.desc()).limit(limit)
        if thesis_id is not None:
            stmt = stmt.where(EntryRow.thesis_id == thesis_id)
        return [Entry(e.id, e.thesis_id, e.kind, e.text, e.created_at) for e in s.scalars(stmt)]
