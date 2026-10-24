"""RSS 2.0 and Atom feeds to items (ADR 0012, 7-8). Metadata only: never article bodies.

Dates: RFC 822 (RSS), ISO 8601 (Atom), and TCMB's localized forms ``22 Eki 2026 14:00:00``
and ``Oct 22, 2026, 2:00:00 PM``. A date without an offset is in the feed's ``naive_tz``
(Europe/Istanbul for TCMB). Parsed with defusedxml (untrusted XML).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element
from zoneinfo import ZoneInfo

import defusedxml.ElementTree as SafeET

from qp.core import QPError, ensure_utc
from qp.news.text import clean

ATOM = "{http://www.w3.org/2005/Atom}"
MAX_SUMMARY = 500
_TR_MONTHS = {
    "oca": 1, "şub": 2, "mar": 3, "nis": 4, "may": 5, "haz": 6,
    "tem": 7, "ağu": 8, "eyl": 9, "eki": 10, "kas": 11, "ara": 12,
}  # fmt: skip
_TR_DATE = re.compile(r"^(\d{1,2}) (\w{3}) (\d{4}) (\d{1,2}):(\d{2}):(\d{2})$")
_EN_DATE = re.compile(r"^(\w{3}) (\d{1,2}), (\d{4}),? (\d{1,2}):(\d{2}):(\d{2})\s?([AP]M)$")
_EN_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


class FeedError(QPError):
    pass


@dataclass(frozen=True, slots=True)
class FeedItem:
    guid: str
    title: str
    link: str
    published_at: datetime  # UTC
    summary: str  # "" unless short and the source allows storing it
    categories: tuple[str, ...]


def parse_date(text: str, naive_tz: str = "UTC") -> datetime:
    """Any of the supported feed date forms -> tz-aware UTC."""
    s = " ".join(text.split())
    tz = ZoneInfo(naive_tz)
    if m := _TR_DATE.fullmatch(s):
        month = _TR_MONTHS.get(m[2].lower().replace("i̇", "i"))
        if month is None:
            raise FeedError(f"unknown Turkish month in {text!r}")
        local = datetime(int(m[3]), month, int(m[1]), int(m[4]), int(m[5]), int(m[6]), tzinfo=tz)
        return ensure_utc(local)
    if m := _EN_DATE.fullmatch(s):
        month = _EN_MONTHS.get(m[1].lower())
        if month is None:
            raise FeedError(f"unknown month in {text!r}")
        hour = int(m[4]) % 12 + (12 if m[7] == "PM" else 0)
        local = datetime(int(m[3]), month, int(m[2]), hour, int(m[5]), int(m[6]), tzinfo=tz)
        return ensure_utc(local)
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(s)
        except (TypeError, ValueError) as exc:
            raise FeedError(f"unparseable date {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def _text(el: Element | None) -> str:
    return clean("".join(el.itertext())) if el is not None else ""


def parse_feed(
    payload: bytes, *, naive_tz: str = "UTC", keep_summary: bool = False
) -> tuple[list[FeedItem], list[str]]:
    """Items and per-item errors (an item with a bad date or no link is skipped)."""
    try:
        root = SafeET.fromstring(payload)
    except Exception as exc:  # defusedxml raises several types for hostile or broken XML
        raise FeedError(f"not a feed: {type(exc).__name__}: {exc}") from exc
    items: list[FeedItem] = []
    errors: list[str] = []
    if root.tag == f"{ATOM}feed":
        entries = root.findall(f"{ATOM}entry")
        for e in entries:
            link_el = next(
                (x for x in e.findall(f"{ATOM}link") if x.get("rel", "alternate") == "alternate"),
                None,
            )
            raw = _Raw(
                _text(e.find(f"{ATOM}id")),
                _text(e.find(f"{ATOM}title")),
                (link_el.get("href") or "") if link_el is not None else "",
                _text(e.find(f"{ATOM}published")) or _text(e.find(f"{ATOM}updated")),
                _text(e.find(f"{ATOM}summary")),
                tuple(c.get("term", "") for c in e.findall(f"{ATOM}category")),
            )
            _add(raw, items, errors, naive_tz, keep_summary)
    elif root.tag == "rss":
        for e in root.findall("./channel/item"):
            raw = _Raw(
                _text(e.find("guid")),
                _text(e.find("title")),
                _text(e.find("link")),
                _text(e.find("pubDate")),
                _text(e.find("description")),
                tuple(_text(c) for c in e.findall("category")),
            )
            _add(raw, items, errors, naive_tz, keep_summary)
    else:
        raise FeedError(f"unknown feed root {root.tag!r}")
    return items, errors


@dataclass(frozen=True, slots=True)
class _Raw:
    guid: str
    title: str
    link: str
    date: str
    summary: str
    categories: tuple[str, ...]


def _add(
    raw: _Raw, items: list[FeedItem], errors: list[str], naive_tz: str, keep_summary: bool
) -> None:
    if not raw.title or not raw.link:
        errors.append(f"item without title or link: {raw.title or raw.link or raw.guid!r}")
        return
    try:
        published = parse_date(raw.date, naive_tz)
    except FeedError as exc:
        errors.append(f"{raw.title[:60]}: {exc}")
        return
    summary = raw.summary
    if not keep_summary or len(summary) > MAX_SUMMARY or summary == raw.title:
        summary = ""
    items.append(
        FeedItem(
            raw.guid or raw.link,
            raw.title,
            raw.link,
            published,
            summary,
            tuple(c for c in raw.categories if c),
        )
    )
