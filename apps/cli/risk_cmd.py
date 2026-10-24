"""``qp risk``: VaR/ES, exposures, concentration and correlation (ADR 0010)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, money, pct, settings
from qp.core import SystemClock, istanbul_date, parse_utc
from qp.core.errors import QPError
from qp.performance.service import load_context
from qp.risk.service import asset_classes, risk_report
from qp.risk.var import VarResult

app = typer.Typer(no_args_is_help=True, help="Risk: VaR/ES, exposures, correlation.")


def _var_table(r: VarResult) -> Table:
    t = Table(
        "method",
        "level",
        "VaR (TRY)",
        "ES (TRY)",
        "VaR % of value",
        title=f"1-day VaR/ES — {r.source} ({r.observations} days, value {money(r.value)})",
    )
    for f in r.figures:
        t.add_row(
            f.method.value,
            pct(f.level, 0),
            money(f.var),
            money(f.es),
            pct(f.var / r.value) if r.value else "—",
        )
    return t


@app.command("show")
def show(
    as_of: Annotated[str | None, typer.Option(help="Day YYYY-MM-DD (default: today)")] = None,
    account: Annotated[list[str] | None, typer.Option(help="Only these accounts")] = None,
    known_at: Annotated[str | None, typer.Option(help="As known at this ISO time")] = None,
    top: Annotated[int, typer.Option(help="Holdings to list")] = 10,
) -> None:
    """Risk report for a day."""
    day = date.fromisoformat(as_of) if as_of else istanbul_date(SystemClock().now())
    try:
        ctx = load_context(settings())
        report = risk_report(
            ctx,
            day=day,
            accounts=account,
            known_at=parse_utc(known_at) if known_at else None,
            classes=asset_classes(ctx),
        )
    except (QPError, ValueError) as exc:
        fail(str(exc))
    for r in (report.portfolio, report.simulation):
        if r is None:
            continue
        if r.unavailable:
            console.print(f"[yellow]{r.source}: unavailable[/yellow] — {r.unavailable}")
        else:
            console.print(_var_table(r))
        for left_out in r.excluded:
            console.print(f"  [yellow]left out:[/yellow] {left_out}")
    if report.volatility is not None:
        console.print(f"Realized volatility (annualized): {pct(report.volatility)}")

    e = report.exposure
    for title, rows in (
        ("By currency", e.by_currency),
        ("By asset class", e.by_asset_class),
        ("By account", e.by_account),
        (f"Top {top} holdings", e.by_holding[:top]),
    ):
        t = Table("name", "value (TRY)", "weight", title=title)
        for x in rows:
            t.add_row(x.name, money(x.value_try), pct(x.weight))
        console.print(t)
    if e.effective_holdings is not None:
        console.print(
            f"Concentration: HHI {e.herfindahl:.4f} "
            f"(≈ {e.effective_holdings:.1f} equal-weight holdings)"
        )
    m = report.correlation
    if len(m.names) > 1 and all(v != v for i, row in enumerate(m.values) for v in row[i + 1 :]):
        console.print("Correlation: not enough shared daily returns (20 needed per pair)")
    elif len(m.names) > 1:
        t = Table("", *m.names, title="Correlation of daily TRY returns")
        for name, row in zip(m.names, m.values, strict=True):
            t.add_row(name, *("—" if v != v else f"{v:.2f}" for v in row))
        console.print(t)
    for note in report.notes:
        console.print(f"[yellow]note:[/yellow] {note}")
    for w in report.holdings.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")
