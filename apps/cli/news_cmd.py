"""``qp news``: fetch official feeds, list items with entities and tags (ADR 0012)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from apps.cli.common import console, fail, settings
from qp.alerts.service import run_news_alerts
from qp.core import SystemClock, assume_istanbul, istanbul_date, parse_utc, to_istanbul
from qp.core.errors import QPError
from qp.news.service import fetch_news
from qp.news.store import query
from qp.platform import Database

app = typer.Typer(no_args_is_help=True, help="News: official feeds, entities, tags.")


@app.command("fetch")
def fetch(
    alerts: Annotated[bool, typer.Option(help="Then send holding-news alerts")] = True,
) -> None:
    """Fetch every enabled feed (configs/base.yaml sources.news.feeds)."""
    cfg = settings()
    try:
        with Database.from_settings(cfg.db) as db:
            report = fetch_news(cfg, db)
            sent = (
                run_news_alerts(cfg, db, day=istanbul_date(SystemClock().now())) if alerts else None
            )
    except QPError as exc:
        fail(str(exc))
    console.print(
        f"feeds fetched {report.fetched}, new items {report.new}, duplicates {report.duplicates}"
    )
    if sent is not None:
        console.print(f"holding-news alerts: {len(sent.new_ids)} new")
    for e in report.errors:
        console.print(f"[yellow]warning:[/yellow] {escape(e)}")
    if report.errors and not report.fetched:
        raise typer.Exit(code=1)


@app.command("list")
def list_items(
    days: Annotated[int, typer.Option(help="Published in the last N days")] = 14,
    source: Annotated[list[str] | None, typer.Option(help="Source code (repeatable)")] = None,
    tag: Annotated[str | None, typer.Option(help="Only this tag")] = None,
    entity: Annotated[
        list[str] | None, typer.Option(help="Entity key, e.g. THYAO.BIST, USD, TCMB")
    ] = None,
    known_at: Annotated[str | None, typer.Option(help="As known at this ISO time")] = None,
    limit: Annotated[int, typer.Option(help="Rows")] = 50,
) -> None:
    """Recent items, newest first."""
    cfg = settings()
    now = SystemClock().now()
    since = assume_istanbul(datetime.combine(istanbul_date(now) - timedelta(days=days), time()))
    try:
        with Database.from_settings(cfg.db) as db:
            items = query(
                db,
                as_of=parse_utc(known_at) if known_at else now,
                since=since,
                sources=source,
                tag_filter=tag,
                entity_keys=entity,
                limit=limit,
            )
    except QPError as exc:
        fail(str(exc))
    t = Table("published (Istanbul)", "source", "title", "entities", "tags")
    for i in items:
        t.add_row(
            f"{to_istanbul(i.published_at):%Y-%m-%d %H:%M}",
            i.source,
            escape(i.title),
            ", ".join(sorted({e.key for e in i.entities})),
            ", ".join(i.tags),
        )
    console.print(t)
    if not items:
        console.print("no items (run `uv run qp news fetch`)")
