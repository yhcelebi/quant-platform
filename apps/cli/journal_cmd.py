"""``qp journal``: theses with invalidation criteria and review dates, notes, watchlists.
Append-only (ADR 0013, 13)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from apps.cli.common import console, fail, settings
from qp.core import SystemClock, istanbul_date, to_istanbul
from qp.core.errors import QPError
from qp.decisions import journal
from qp.platform import Database

app = typer.Typer(no_args_is_help=True, help="Decisions journal (append-only).")


def _db() -> Database:
    return Database.from_settings(settings().db)


@app.command("add")
def add(
    title: Annotated[str, typer.Option(help="Short title")],
    thesis: Annotated[str, typer.Option(help="What you believe and why")],
    invalidation: Annotated[str, typer.Option(help="What would prove it wrong")],
    review_on: Annotated[str, typer.Option(help="First review date YYYY-MM-DD")],
    direction: Annotated[str, typer.Option(help="long | short | neutral | macro")] = "long",
    instrument: Annotated[str | None, typer.Option(help="SYMBOL.VENUE, if any")] = None,
) -> None:
    """Record a thesis (cannot be edited later: review it instead)."""
    try:
        with _db() as db:
            tid = journal.add_thesis(
                db,
                title=title,
                thesis=thesis,
                invalidation=invalidation,
                first_review=date.fromisoformat(review_on),
                direction=direction,
                instrument=instrument,
            )
    except (QPError, ValueError) as exc:
        fail(str(exc))
    console.print(f"thesis #{tid} recorded; first review {review_on}")


@app.command("list")
def list_cmd(all_: Annotated[bool, typer.Option("--all", help="Include closed")] = False) -> None:
    """Theses with status and next review date."""
    try:
        with _db() as db:
            rows = journal.theses(db)
    except QPError as exc:
        fail(str(exc))
    today = istanbul_date(SystemClock().now())
    t = Table("id", "title", "instrument", "dir", "status", "next review", "reviews")
    for r in rows:
        if not all_ and r.status != "open":
            continue
        nxt = str(r.next_review or "—")
        if r.next_review and r.next_review <= today:
            nxt = f"[yellow]{nxt} (due)[/yellow]"
        t.add_row(
            str(r.id),
            escape(r.title),
            r.instrument or "—",
            r.direction,
            r.status,
            nxt,
            str(r.reviews),
        )
    console.print(t)


@app.command("show")
def show(thesis_id: Annotated[int, typer.Argument(help="Thesis id")]) -> None:
    """A thesis, its invalidation criteria and its notes."""
    try:
        with _db() as db:
            t = next((x for x in journal.theses(db) if x.id == thesis_id), None)
            notes = journal.entries(db, thesis_id)
    except QPError as exc:
        fail(str(exc))
    if t is None:
        fail(f"no thesis {thesis_id}")
    console.print(f"[bold]#{t.id} {escape(t.title)}[/bold] ({t.direction}, {t.status})")
    console.print(f"Thesis: {escape(t.thesis)}\nInvalidation: {escape(t.invalidation)}")
    console.print(
        f"Next review: {t.next_review or '—'}; last review note: {escape(t.last_note) or '—'}"
    )
    for e in notes:
        console.print(f"- {to_istanbul(e.created_at):%Y-%m-%d %H:%M} [{e.kind}] {escape(e.text)}")


@app.command("review")
def review(
    thesis_id: Annotated[int, typer.Argument(help="Thesis id")],
    verdict: Annotated[str, typer.Option(help="keep | close | invalidated")],
    note: Annotated[str, typer.Option(help="What you checked")] = "",
    next_review: Annotated[str | None, typer.Option(help="With keep: next date")] = None,
) -> None:
    """Review a thesis against its invalidation criteria."""
    try:
        with _db() as db:
            journal.review(
                db,
                thesis_id,
                verdict=verdict,
                note=note,
                next_review=date.fromisoformat(next_review) if next_review else None,
            )
    except (QPError, ValueError) as exc:
        fail(str(exc))
    console.print(f"thesis #{thesis_id}: {verdict}")


@app.command("note")
def note(
    text: Annotated[str, typer.Argument(help="The note")],
    thesis: Annotated[int | None, typer.Option(help="Attach to a thesis")] = None,
    decision: Annotated[bool, typer.Option(help="Record as a decision")] = False,
) -> None:
    """A journal note or decision."""
    try:
        with _db() as db:
            eid = journal.add_entry(
                db, text, kind="decision" if decision else "note", thesis_id=thesis
            )
    except QPError as exc:
        fail(str(exc))
    console.print(f"entry #{eid} recorded")


@app.command("watch")
def watch(
    name: Annotated[str, typer.Argument(help="Watchlist name")],
    instrument: Annotated[str, typer.Argument(help="SYMBOL.VENUE")],
    remove: Annotated[bool, typer.Option(help="Remove instead of add")] = False,
) -> None:
    """Add to (or remove from) a watchlist."""
    try:
        with _db() as db:
            journal.watch(db, name, instrument, remove=remove)
            lists = journal.watchlists(db)
    except QPError as exc:
        fail(str(exc))
    console.print(f"{name}: {', '.join(lists.get(name, [])) or 'empty'}")
