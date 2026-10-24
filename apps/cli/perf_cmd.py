"""``qp perf``: returns, benchmarks and P&L attribution (ADR 0010)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, money, pct, settings
from qp.core import SystemClock, istanbul_date, parse_utc
from qp.core.errors import QPError
from qp.performance.metrics import VIEW_LABEL, Summary, Unavailable, View
from qp.performance.service import (
    attribution_report,
    default_start,
    load_context,
    performance_report,
)

app = typer.Typer(no_args_is_help=True, help="Performance: TWR, XIRR, benchmarks, attribution.")

AccountOpt = Annotated[list[str] | None, typer.Option(help="Only these accounts (repeatable)")]
KnownAtOpt = Annotated[
    str | None, typer.Option(help="As known at this ISO time (with offset): ledger and data")
]


def _day(text: str | None) -> date:
    return date.fromisoformat(text) if text else istanbul_date(SystemClock().now())


@app.command("show")
def show(
    end: Annotated[str | None, typer.Option(help="Last day YYYY-MM-DD (default: today)")] = None,
    start: Annotated[
        str | None, typer.Option(help="Start: the close of this day (default: first activity)")
    ] = None,
    view: Annotated[View, typer.Option(help="try | usd | real")] = View.TRY,
    account: AccountOpt = None,
    known_at: KnownAtOpt = None,
    months: Annotated[int, typer.Option(help="Monthly returns to list")] = 12,
) -> None:
    """Performance summary, standard periods, benchmarks and monthly returns."""
    try:
        ctx = load_context(settings())
        report = performance_report(
            ctx,
            end=_day(end),
            start=date.fromisoformat(start) if start else None,
            view=view,
            accounts=account,
            known_at=parse_utc(known_at) if known_at else None,
        )
    except (QPError, ValueError) as exc:
        fail(str(exc))
    unit = VIEW_LABEL[view]
    s = report.summary
    if isinstance(s, Unavailable):
        console.print(f"[yellow]Performance unavailable:[/yellow] {s.reason}")
        if report.inflation_until:
            console.print(f"Inflation index available to {report.inflation_until}")
        raise typer.Exit(code=1)

    t = Table("", "", title=f"Performance {s.start} → {s.end} ({unit})", show_header=False)
    t.add_row("Start value", money(s.start_value))
    t.add_row("Net external flows", money(s.net_flows))
    t.add_row("End value", money(s.end_value))
    t.add_row("P&L", money(s.pnl))
    t.add_row("TWR", pct(s.twr))
    t.add_row(
        "TWR annualized", pct(s.twr_annualized) if s.twr_annualized is not None else "— (< 1 year)"
    )
    if s.days >= 365:
        t.add_row("XIRR (money-weighted, annual)", pct(s.xirr))
    else:
        t.add_row("Money-weighted (period, XIRR de-annualized)", pct(s.mwr_period))
    t.add_row("Volatility (annualized)", pct(s.volatility))
    if s.max_drawdown:
        dd = s.max_drawdown
        rec = str(dd.recovery) if dd.recovery else "not recovered"
        t.add_row("Max drawdown", f"{pct(dd.depth)} ({dd.peak} → {dd.trough}, {rec})")
    t.add_row("Current drawdown", pct(s.current_drawdown))
    console.print(t)

    periods = Table("period", "start", "TWR", "annualized", f"P&L ({unit})", title="Periods")
    for label, p in report.periods.items():
        if isinstance(p, Summary):
            periods.add_row(label, str(p.start), pct(p.twr), pct(p.twr_annualized), money(p.pnl))
        else:
            periods.add_row(label, "—", "—", "—", f"[dim]{p.reason}[/dim]")
    console.print(periods)

    bench = Table("benchmark", f"return ({unit})", "vs portfolio", title="Benchmarks, same days")
    for spec, r in report.benchmarks:
        if isinstance(r, Unavailable):
            bench.add_row(spec.name, "—", f"[dim]{r.reason}[/dim]")
        else:
            bench.add_row(spec.name, pct(r.total), pct(s.twr - r.total))
    console.print(bench)

    if report.monthly and months > 0:
        mt = Table("month", "TWR", title=f"Monthly returns ({unit})")
        for (y, m), ret in sorted(report.monthly.items())[-months:]:
            mt.add_row(f"{y}-{m:02d}", pct(ret))
        console.print(mt)
    for note in s.notes:
        console.print(f"[yellow]note:[/yellow] {note}")
    if s.stale:
        shown = ", ".join(s.stale[:5]) + (" …" if len(s.stale) > 5 else "")
        console.print(f"[yellow]stale values used ({len(s.stale)}):[/yellow] {shown}")


@app.command("attribution")
def attribution(
    end: Annotated[str | None, typer.Option(help="Last day YYYY-MM-DD (default: today)")] = None,
    start: Annotated[
        str | None, typer.Option(help="Start: the close of this day (default: 31 Dec)")
    ] = None,
    account: AccountOpt = None,
    known_at: KnownAtOpt = None,
) -> None:
    """P&L by instrument, cash currency and fees/interest/taxes, with the FX/price split."""
    day = _day(end)
    first = date.fromisoformat(start) if start else default_start(day)
    try:
        result = attribution_report(
            load_context(settings()),
            start=first,
            end=day,
            accounts=account,
            known_at=parse_utc(known_at) if known_at else None,
        )
    except (QPError, ValueError) as exc:
        fail(str(exc))
    if isinstance(result, Unavailable):
        console.print(f"[yellow]Attribution unavailable:[/yellow] {result.reason}")
        raise typer.Exit(code=1)
    t = Table(
        "bucket",
        "start",
        "end",
        "inflow",
        "P&L",
        "price effect",
        "FX effect",
        title=f"P&L attribution {result.start} → {result.end} (TRY)",
    )
    for r in result.rows:
        t.add_row(
            r.label,
            money(r.start_value),
            money(r.end_value),
            money(r.inflow),
            money(r.pnl),
            money(r.price_effect),
            money(r.fx_effect),
        )
    t.add_row(
        "[bold]total[/bold]",
        money(result.start_value),
        money(result.end_value),
        money(result.external_flows),
        f"[bold]{money(result.total_pnl)}[/bold]",
        "",
        "",
    )
    console.print(t)
    if result.residual:
        console.print(f"[red]residual {result.residual} (should be 0)[/red]")
    if result.stale:
        console.print(f"[yellow]stale values used:[/yellow] {', '.join(result.stale[:5])}")
