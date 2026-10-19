"""``qp ref``: reference data (venues, instruments)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, qty, settings
from qp.core import SystemClock, to_istanbul
from qp.core.errors import QPError
from qp.platform import Database
from qp.reference import candidates as cands
from qp.reference.importer import plan_instrument_import
from qp.reference.repo import ReferenceRepository

app = typer.Typer(
    no_args_is_help=True, help="Reference data: venues, instruments, corporate actions."
)
actions_app = typer.Typer(no_args_is_help=True, help="Corporate actions and the review queue.")
app.add_typer(actions_app, name="actions")


@app.command("venues")
def venues() -> None:
    """List venues."""
    with Database.from_settings(settings().db) as db, db.session() as s:
        rows = ReferenceRepository(s).venues()
    table = Table("code", "name", "timezone", "country", "currency")
    for v in rows:
        table.add_row(
            v.code, v.name, v.timezone, v.country or "", v.currency.code if v.currency else ""
        )
    console.print(table)


@app.command("instruments")
def instruments() -> None:
    """List instruments."""
    with Database.from_settings(settings().db) as db, db.session() as s:
        rows = ReferenceRepository(s).instruments()
    table = Table("id", "asset_class", "currency", "name", "isin")
    for i in rows:
        table.add_row(str(i.id), i.asset_class.value, i.currency.code, i.name, i.isin or "")
    console.print(table)


@app.command("import")
def import_instruments(
    file: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="CSV: id,asset_class,currency,name,isin"),
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate only; write nothing.")
    ] = False,
) -> None:
    """Import instruments from CSV. Existing identical rows are skipped."""
    with Database.from_settings(settings().db) as db, db.session() as s:
        repo = ReferenceRepository(s)
        existing = {i.id: i for i in repo.instruments()}
        plan = plan_instrument_import(
            file.read_text(encoding="utf-8"), existing, {v.code for v in repo.venues()}
        )
        for err in plan.errors:
            console.print(f"[red]{err}[/red]")
        console.print(
            f"new: {len(plan.new)}, unchanged: {len(plan.unchanged)}, errors: {len(plan.errors)}"
        )
        if not plan.ok:
            fail("nothing imported")
        if dry_run:
            console.print("[yellow]dry run: nothing written[/yellow]")
            return
        for inst in plan.new:
            repo.add_instrument(inst)
    console.print(f"[green]imported {len(plan.new)} instruments[/green]")


@actions_app.command("list")
def actions_list(
    status: Annotated[
        str, typer.Option(help="pending | accepted | rejected | all (review queue)")
    ] = "pending",
) -> None:
    """Corporate-action candidates reported by data sources and not yet recorded."""
    if status not in ("pending", "accepted", "rejected", "all"):
        fail("status must be pending, accepted, rejected or all")
    with Database.from_settings(settings().db) as db, db.session() as s:
        rows = cands.candidates(s, None if status == "all" else status)
        table = Table("id", "instrument", "type", "ex_date", "ratio", "per share", "source",
                      "reported (Istanbul)", "status", "note")  # fmt: skip
        for r in rows:
            table.add_row(
                str(r.id), r.instrument_id, r.action_type, str(r.ex_date), qty(r.ratio),
                f"{qty(r.amount_per_share)} {r.currency or ''}".strip(), r.source,
                to_istanbul(r.reported_at).strftime("%Y-%m-%d %H:%M"), r.status, r.note,
            )  # fmt: skip
    console.print(table if rows else f"no {status} candidates")


@actions_app.command("accept")
def actions_accept(
    candidate_id: Annotated[int, typer.Argument(help="Candidate id")],
    note: Annotated[str, typer.Option(help="Why (e.g. checked against KAP disclosure)")],
) -> None:
    """Record a candidate as a corporate action (append-only; cannot be undone)."""
    try:
        with Database.from_settings(settings().db) as db, db.session() as s:
            action = cands.accept(s, candidate_id, note=note, now=SystemClock().now())
    except QPError as exc:
        fail(str(exc))
    console.print(f"recorded corporate action {action.id}: {action.instrument} "
                  f"{action.action_type} ex {action.ex_date}")  # fmt: skip


@actions_app.command("reject")
def actions_reject(
    candidate_id: Annotated[int, typer.Argument(help="Candidate id")],
    note: Annotated[str, typer.Option(help="Why it is wrong")],
) -> None:
    """Reject a candidate (kept, with your note)."""
    try:
        with Database.from_settings(settings().db) as db, db.session() as s:
            cands.reject(s, candidate_id, note=note, now=SystemClock().now())
    except QPError as exc:
        fail(str(exc))
    console.print(f"rejected candidate {candidate_id}")
