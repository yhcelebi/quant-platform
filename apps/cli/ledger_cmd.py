"""``qp ledger``: accounts, CSV import, listing, reversals and reconciliation."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from apps.cli.common import console, fail, money, qty, settings
from qp.core import Currency, SystemClock, to_istanbul
from qp.core.errors import QPError
from qp.ledger.domain import Account, AccountPurpose, AccountType, LegKind
from qp.ledger.engine import LedgerState
from qp.ledger.importer import commit_import, plan_import_from_db
from qp.ledger.repo import LedgerRepository
from qp.ledger.service import add_account, reverse
from qp.platform import Database
from qp.platform.settings import default_config_dir
from qp.portfolio.service import reconcile_statement
from qp.reference.rates import RateTable

app = typer.Typer(no_args_is_help=True, help="The append-only ledger.")
account_app = typer.Typer(no_args_is_help=True, help="Accounts.")
app.add_typer(account_app, name="account")


@account_app.command("add")
def account_add(
    code: Annotated[str, typer.Argument(help="Short unique code, e.g. ISY-MAIN")],
    name: Annotated[str, typer.Option(help="Display name")],
    currency: Annotated[str, typer.Option(help="Base currency (ISO 4217)")] = "TRY",
    account_type: Annotated[AccountType, typer.Option("--type")] = AccountType.BROKERAGE,
    purpose: Annotated[AccountPurpose, typer.Option()] = AccountPurpose.LONG_TERM,
    broker: Annotated[str, typer.Option()] = "",
) -> None:
    """Create an account (with its default sleeve 'main')."""
    try:
        acct = Account(code, name, account_type, purpose, Currency(currency), broker)
        with Database.from_settings(settings().db) as db, db.session() as s:
            add_account(s, acct)
    except QPError as exc:
        fail(str(exc))
    console.print(f"[green]account {code} created[/green] ({purpose.value})")


@account_app.command("list")
def account_list() -> None:
    """List accounts."""
    with Database.from_settings(settings().db) as db, db.session() as s:
        rows = LedgerRepository(s).accounts()
    table = Table("code", "name", "type", "purpose", "currency", "broker")
    for a in rows:
        table.add_row(
            a.code, a.name, a.account_type.value, a.purpose.value, a.base_currency.code, a.broker
        )
    console.print(table)


def _changes(before: LedgerState, after: LedgerState) -> Table:
    table = Table("account", "item", "before", "after", title="Resulting changes")
    b_pos = {(p.account, str(p.instrument)): p.quantity for p in before.open_positions()}
    a_pos = {(p.account, str(p.instrument)): p.quantity for p in after.open_positions()}
    for key in sorted(set(b_pos) | set(a_pos)):
        if b_pos.get(key) != a_pos.get(key):
            table.add_row(key[0], key[1], qty(b_pos.get(key)), qty(a_pos.get(key)))
    b_cash = {(a, c.code): v for (a, c), v in before.cash_balances().items()}
    a_cash = {(a, c.code): v for (a, c), v in after.cash_balances().items()}
    for key in sorted(set(b_cash) | set(a_cash)):
        if b_cash.get(key) != a_cash.get(key):
            table.add_row(key[0], f"cash {key[1]}", money(b_cash.get(key)), money(a_cash.get(key)))
    return table


@app.command("import")
def import_file(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Canonical ledger CSV")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate and simulate; write nothing.")
    ] = False,
) -> None:
    """Import a canonical ledger CSV (docs/ledger-csv-format.md). All rows or none."""
    cfg = settings()
    rates = RateTable.from_dir(default_config_dir() / "rates")
    with Database.from_settings(cfg.db) as db, db.session() as s:
        plan = plan_import_from_db(
            s, file.read_bytes(), file.name, now=SystemClock().now(), rates=rates
        )
        for err in plan.errors:
            console.print(f"[red]{err}[/red]")
        for warning in plan.warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")
        if not plan.ok:
            fail(f"{len(plan.errors)} error(s); nothing imported")
        assert plan.before is not None
        assert plan.after is not None
        console.print(
            f"{plan.row_count} rows -> {len(plan.transactions)} transactions, "
            f"{len(plan.new_actions)} new corporate actions"
        )
        console.print(_changes(plan.before, plan.after))
        if dry_run:
            console.print("[yellow]dry run: nothing written[/yellow]")
            return
        stored = commit_import(s, plan)
    console.print(
        f"[green]imported {len(stored)} transactions[/green] (file sha256 {plan.sha256[:12]})"
    )


@app.command("list")
def list_transactions(
    account: Annotated[str | None, typer.Option(help="Only this account")] = None,
    limit: Annotated[int, typer.Option(help="Most recent N")] = 50,
) -> None:
    """List recent transactions (reversed ones are marked)."""
    with Database.from_settings(settings().db) as db, db.session() as s:
        txs = LedgerRepository(s).transactions()
    reversed_ids = {t.reverses_id for t in txs if t.reverses_id is not None}
    rows = [t for t in txs if account is None or t.account == account][-limit:]
    table = Table(
        "id",
        "date",
        "account",
        "type",
        "instrument",
        "quantity",
        "cash",
        "recorded (Istanbul)",
        "note",
    )
    for t in rows:
        pos = t.legs_of(LegKind.POSITION)
        cash = ", ".join(f"{money(leg.amount)} {leg.currency}" for leg in t.legs_of(LegKind.CASH))
        kind = t.tx_type.value + (" (reversed)" if t.id in reversed_ids else "")
        if t.reverses_id is not None:
            kind += f" of #{t.reverses_id}"
        table.add_row(
            str(t.id),
            str(t.trade_date),
            t.account,
            kind,
            ", ".join(str(p.instrument) for p in pos),
            ", ".join(qty(p.quantity) for p in pos),
            cash,
            to_istanbul(t.recorded_at).strftime("%Y-%m-%d %H:%M"),
            escape(t.note),
        )
    console.print(table)


@app.command("reverse")
def reverse_transaction(
    tx_id: Annotated[int, typer.Argument(help="Transaction id to cancel")],
    note: Annotated[str, typer.Option(help="Why it is being reversed")],
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation")] = False,
) -> None:
    """Append a reversal cancelling a transaction (the original is kept; rule 13)."""
    if not yes and not typer.confirm(f"Reverse transaction {tx_id}?"):
        fail("aborted")
    try:
        with Database.from_settings(settings().db) as db, db.session() as s:
            rev = reverse(s, tx_id, note=note, now=SystemClock().now())
    except QPError as exc:
        fail(str(exc))
    console.print(f"[green]reversal #{rev.id} recorded[/green] for transaction {tx_id}")


@app.command("reconcile")
def reconcile_cmd(
    statement: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Broker snapshot CSV")
    ],
    as_of: Annotated[str, typer.Option(help="Statement date YYYY-MM-DD")],
    no_cash: Annotated[bool, typer.Option("--no-cash", help="Compare positions only")] = False,
) -> None:
    """Compare ledger holdings with a broker statement snapshot. Exit 1 on any mismatch."""
    try:
        mismatches = reconcile_statement(
            settings(),
            statement.read_text(encoding="utf-8"),
            as_of=date.fromisoformat(as_of),
            include_cash=not no_cash,
        )
    except (QPError, ValueError) as exc:
        fail(str(exc))
    if not mismatches:
        console.print("[green]ledger matches the statement[/green]")
        return
    table = Table("account", "item", "ledger", "statement", "difference", title="Mismatches")
    for m in mismatches:
        table.add_row(m.account, m.item, qty(m.ledger), qty(m.statement), qty(m.difference))
    console.print(table)
    raise typer.Exit(code=1)
