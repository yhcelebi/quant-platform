"""``qp portfolio``: holdings and P&L at any date."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, money, qty, settings
from qp.core import SystemClock, istanbul_date, parse_utc
from qp.core.errors import QPError
from qp.portfolio.holdings import CostMethod
from qp.portfolio.service import load_report

app = typer.Typer(no_args_is_help=True, help="Holdings and P&L.")


@app.command("show")
def show(
    as_of: Annotated[
        str | None, typer.Option(help="Date YYYY-MM-DD (default: today, Istanbul)")
    ] = None,
    known_at: Annotated[
        str | None, typer.Option(help="Show the ledger as known at this ISO time (with offset)")
    ] = None,
    account: Annotated[
        list[str] | None, typer.Option(help="Only these accounts (repeatable)")
    ] = None,
    currency: Annotated[str, typer.Option(help="Display currency: TRY or USD")] = "TRY",
    method: Annotated[CostMethod, typer.Option(help="Cost basis method")] = CostMethod.AVERAGE,
    stale_days: Annotated[int, typer.Option(help="Flag prices older than this")] = 7,
) -> None:
    """Show holdings, cash, unrealized and year-to-date realized P&L."""
    if currency not in ("TRY", "USD"):
        fail("currency must be TRY or USD")
    try:
        now = SystemClock().now()
        day = date.fromisoformat(as_of) if as_of else istanbul_date(now)
        known: datetime | None = parse_utc(known_at) if known_at else None
        report, _ = load_report(
            settings(),
            as_of=day,
            known_at=known,
            accounts=account,
            method=method,
            stale_after_days=stale_days,
        )
    except (QPError, ValueError) as exc:
        fail(str(exc))

    usd = currency == "USD"
    rate = report.usd_try.rate_try if report.usd_try else None
    if usd and rate is None:
        fail("no USD/TRY rate available for the USD view")

    def conv(v_try: Decimal | None) -> str:
        if v_try is None:
            return "—"
        return money(v_try / rate if usd and rate else v_try)

    title = f"Holdings {report.as_of} ({method.value} cost, values in {currency})"
    table = Table(
        "account",
        "instrument",
        "qty",
        "avg cost",
        "price",
        "price date",
        "value",
        "cost",
        "unrealized",
        "FX effect",
        "price effect",
        title=title,
    )
    for row in report.positions:
        v = row.valuation
        cost_view = v.cost_try / rate if usd and rate else v.cost_try
        table.add_row(
            v.account,
            str(v.instrument),
            qty(v.quantity),
            f"{money(v.avg_cost, 4)} {v.currency}",
            money(v.price, 4),
            str(row.price.valid_date) + (" (stale)" if row.stale else "") if row.price else "—",
            conv(v.value_try),
            money(cost_view),
            conv(v.unrealized_try),
            conv(v.fx_effect_try),
            conv(v.price_effect_try),
        )
    console.print(table)

    cash = Table("account", "currency", "amount", f"value ({currency})", title="Cash")
    for c in report.cash:
        cash.add_row(c.account, c.currency.code, money(c.amount), conv(c.amount_try))
    console.print(cash)

    total = report.total_usd if usd else report.total_try
    marker = "" if report.complete else " [yellow](incomplete: some values missing)[/yellow]"
    console.print(f"[bold]Total {currency}: {money(total)}[/bold]{marker}")
    if report.usd_try:
        fx = report.usd_try
        console.print(f"USD/TRY {fx.rate_try} ({fx.valid_date}, {fx.source})")
    realized_try = sum((r.realized_try for r in report.realized), Decimal(0))
    income_net_try = sum((i.net * i.fx_rate_try for i in report.income), Decimal(0))
    console.print(
        f"Year-to-date realized P&L (TRY): {money(realized_try)}; "
        f"net income (TRY): {money(income_net_try)}"
    )
    for w in report.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")
