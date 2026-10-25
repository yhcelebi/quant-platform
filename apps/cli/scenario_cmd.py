"""``qp scenario``: stress scenarios on today's holdings (ADR 0016)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, money, pct, settings
from qp.core import SystemClock, istanbul_date, parse_utc
from qp.core.errors import QPError
from qp.performance.service import load_context
from qp.risk.service import asset_classes
from qp.scenarios.definitions import load
from qp.scenarios.service import scenario_report

app = typer.Typer(no_args_is_help=True, help="Stress scenarios: shocks and replays.")


@app.command("list")
def list_cmd() -> None:
    """Scenarios defined in configs/scenarios.yaml."""
    cfg = settings()
    try:
        defs = load(cfg.scenarios.file)
    except QPError as exc:
        fail(str(exc))
    t = Table("name", "kind", "definition", title=str(cfg.scenarios.file))
    for s in defs.shocks:
        parts = [f"{c} {v:+.1%}" for c, v in s.fx.items()]
        parts += [f"{k} {v:+.1%}" for k, v in {**s.venue, **s.asset_class}.items()]
        parts += [f"{i} {v:+.1%}" for i, v in s.instrument.items()]
        if s.rate_bp:
            parts.append(f"rates {s.rate_bp:+}bp")
        t.add_row(s.name, "shock", ", ".join(parts))
    for r in defs.replays:
        t.add_row(r.name, "replay", f"{r.start} → {r.end}")
    for k in cfg.scenarios.worst_windows:
        t.add_row(f"worst {k}-day", "worst", "searched over the available history")
    console.print(t)


@app.command("run")
def run(
    name: Annotated[list[str] | None, typer.Option(help="Only these scenarios")] = None,
    as_of: Annotated[str | None, typer.Option(help="Day YYYY-MM-DD (default: today)")] = None,
    account: Annotated[list[str] | None, typer.Option(help="Only these accounts")] = None,
    known_at: Annotated[str | None, typer.Option(help="As known at this ISO time")] = None,
    detail: Annotated[bool, typer.Option(help="Show every holding")] = False,
) -> None:
    """P&L of today's holdings under each scenario."""
    day = date.fromisoformat(as_of) if as_of else istanbul_date(SystemClock().now())
    try:
        ctx = load_context(settings())
        rep = scenario_report(
            ctx,
            day=day,
            accounts=account,
            known_at=parse_utc(known_at) if known_at else None,
            classes=asset_classes(ctx),
            names=name,
        )
    except (QPError, ValueError) as exc:
        fail(str(exc))
    t = Table(
        "scenario",
        "window",
        "P&L (TRY)",
        "change",
        "covered",
        title=f"Scenarios on {day} holdings ({money(rep.value_try)} TRY)",
    )
    for r in rep.results:
        window = f"{r.window[0]} → {r.window[1]}" if r.window else ""
        covered = pct(r.covered_try / rep.value_try) if rep.value_try else "—"
        if r.unavailable:
            t.add_row(r.name, window, "—", "—", r.unavailable)
        else:
            t.add_row(r.name, window, money(r.pnl), pct(r.change), covered)
    console.print(t)
    for r in rep.results:
        if detail:
            d = Table("holding", "value (TRY)", "change", "P&L (TRY)", "how", title=r.name)
            for row in r.rows:
                d.add_row(row.name, money(row.value_try), pct(row.change), money(row.pnl), row.note)
            console.print(d)
        for m in r.missing:
            console.print(f"[yellow]{r.name}:[/yellow] {m.name} left out ({m.note or 'no data'})")
        for n in r.notes:
            console.print(f"[yellow]{r.name}:[/yellow] {n}")
    for n in rep.notes:
        console.print(f"[yellow]note:[/yellow] {n}")
