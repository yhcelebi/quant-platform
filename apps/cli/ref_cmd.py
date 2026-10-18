"""``qp ref``: reference data (venues, instruments)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, settings
from qp.platform import Database
from qp.reference.importer import plan_instrument_import
from qp.reference.repo import ReferenceRepository

app = typer.Typer(no_args_is_help=True, help="Reference data: venues and instruments.")


@app.command("venues")
def venues() -> None:
    """List venues."""
    db = Database.from_settings(settings().db)
    with db.session() as s:
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
    db = Database.from_settings(settings().db)
    with db.session() as s:
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
    db = Database.from_settings(settings().db)
    with db.session() as s:
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
