"""``qp alerts``: evaluate alert rules, store new events once, send them (ADR 0010, 17)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, settings
from qp.alerts.service import run_alerts
from qp.alerts.store import recent
from qp.core import SystemClock, istanbul_date, to_istanbul
from qp.core.errors import QPError
from qp.platform import Database

app = typer.Typer(no_args_is_help=True, help="Alerts: daily loss, drawdown, VaR breach, prices.")


@app.command("run")
def run(
    as_of: Annotated[str | None, typer.Option(help="Day YYYY-MM-DD (default: today)")] = None,
    dry_run: Annotated[bool, typer.Option(help="Only show what would alert")] = False,
) -> None:
    """Evaluate the rules for a day; new alerts are stored and sent (Telegram or the log)."""
    day = date.fromisoformat(as_of) if as_of else istanbul_date(SystemClock().now())
    cfg = settings()
    try:
        with Database.from_settings(cfg.db) as db:
            result = run_alerts(cfg, db, day=day, dry_run=dry_run)
    except (QPError, ValueError) as exc:
        fail(str(exc))
    t = Table("rule", "subject", "key date", "level", "message", title=f"Alerts for {day}")
    for c in result.candidates:
        t.add_row(c.rule, c.subject, str(c.key_date), c.level, c.message)
    console.print(t)
    if dry_run:
        console.print("dry run: nothing stored or sent")
    else:
        old = len(result.candidates) - len(result.new_ids)
        console.print(f"{len(result.new_ids)} new, {old} already sent")
    for note in result.notes:
        console.print(f"[yellow]note:[/yellow] {note}")
    for err in result.errors:
        console.print(f"[red]delivery failed:[/red] {err}")
    if result.errors:
        raise typer.Exit(code=1)


@app.command("list")
def list_events(limit: Annotated[int, typer.Option(help="Rows")] = 30) -> None:
    """Recent alert events."""
    cfg = settings()
    try:
        with Database.from_settings(cfg.db) as db:
            rows = recent(db, limit)
    except QPError as exc:
        fail(str(exc))
    t = Table("id", "created (Istanbul)", "rule", "subject", "level", "sent", "message")
    for r in rows:
        sent = (
            "yes" if r.sent_at else (f"[red]{r.delivery_error}[/red]" if r.delivery_error else "no")
        )
        t.add_row(
            str(r.id),
            f"{to_istanbul(r.created_at):%Y-%m-%d %H:%M}",
            r.rule,
            r.subject,
            r.level,
            sent,
            r.message,
        )
    console.print(t)
