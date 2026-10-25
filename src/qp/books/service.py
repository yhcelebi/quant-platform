"""Wiring for apps: the books folder, TCMB rates for TRY conversion, import and review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from beancount.core import data

from qp.books.config import load_rules, load_sources
from qp.books.importer import ImportPlan, needs_review, plan_import, recategorize
from qp.books.ledger import Books, BooksError, load, write
from qp.books.reader import statement_rows
from qp.books.statements import Rates
from qp.core import Currency
from qp.data.service import market_data
from qp.platform.settings import Settings, default_config_dir

LATEST = datetime.max.replace(tzinfo=UTC)


def root(settings: Settings) -> Path:
    return settings.books.root


def templates() -> Path:
    return default_config_dir() / "books"


def tcmb_rates(settings: Settings, *, as_of: datetime = LATEST) -> Rates:
    """TRY per unit of a currency on a day: the latest TCMB rate on or before it, at most
    ``books.max_rate_gap_days`` old (weekends and holidays use Friday's rate)."""
    market = market_data(settings)
    gap = settings.books.max_rate_gap_days
    cache: dict[tuple[str, date], Decimal | None] = {}

    def rate(currency: str, day: date) -> Decimal | None:
        key = (currency, day)
        if key not in cache:
            p = market.fx_try(Currency(currency), day, as_of=as_of)
            cache[key] = p.rate_try if p is not None and (day - p.valid_date).days <= gap else None
        return cache[key]

    return rate


@dataclass(frozen=True, slots=True)
class ImportResult:
    plan: ImportPlan
    written: bool


def import_file(settings: Settings, path: Path, source_name: str, *, dry_run: bool) -> ImportResult:
    books = load(root(settings))
    if books.errors:
        raise BooksError("fix the books first: " + "; ".join(books.errors[:3]))
    sources = load_sources(books.root / "sources.yaml")
    if source_name not in sources:
        raise BooksError(f"unknown source {source_name!r}; known: {', '.join(sorted(sources))}")
    source = sources[source_name]
    rows, skipped = statement_rows(path, source)
    plan = plan_import(books, source, rows, load_rules(books.root / "rules.yaml"), skipped=skipped)
    if dry_run or not plan.new:
        return ImportResult(plan, False)
    write(
        books,
        plan.new,
        message=f"import {source_name} {path.name}: {len(plan.new)} new, "
        f"{plan.uncategorized} to review",
    )
    return ImportResult(plan, True)


def review_queue(books: Books) -> list[data.Transaction]:
    return sorted((t for t in books.transactions() if needs_review(t)), key=lambda t: t.date)


def find(books: Books, import_id: str) -> data.Transaction:
    hits = [
        t for t in books.transactions() if str(t.meta.get("import_id", "")).startswith(import_id)
    ]
    if len(hits) != 1:
        raise BooksError(f"{len(hits)} transactions match id {import_id!r}")
    return hits[0]


def set_category(
    settings: Settings, import_id: str, account: str, *, payee: str | None = None
) -> data.Transaction:
    books = load(root(settings))
    t = find(books, import_id)
    if "import_id" not in t.meta:
        raise BooksError("only qp-written transactions can be changed here")
    new = recategorize(t, books, account, payee=payee)
    write(books, replace={t.meta["import_id"]: new}, message=f"categorize {import_id} as {account}")
    return new
