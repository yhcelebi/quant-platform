"""``qp trader``: deployments, promotions, daily paper cycles, kill switch (ADR 0015).

There is no live path here. ``approved_live`` can only be recorded by the owner at an
interactive terminal with a typed confirmation (AGENTS.md: never by an agent).
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, money, settings
from qp.core import SystemClock
from qp.core.errors import QPError
from qp.execution.service import run_paper
from qp.platform import Database
from qp.reporting.paper import paper_vs_backtest
from qp.risk import gate
from qp.strategies.promotion import deploy, deployments, promote

app = typer.Typer(no_args_is_help=True, help="Paper trading: deploy, promote, run, kill switch.")


def _db() -> Database:
    return Database.from_settings(settings().db)


@app.command("deploy")
def deploy_cmd(
    name: Annotated[str, typer.Argument(help="Lower-case name, e.g. usd-sma")],
    signal: Annotated[str, typer.Option(help="Registered signal")],
    costs: Annotated[str, typer.Option(help="Cost profile (backtest.costs)")],
    capital: Annotated[str, typer.Option(help="Paper capital in the trading currency")],
    instrument: Annotated[str | None, typer.Option(help="SYMBOL.VENUE")] = None,
    fx: Annotated[str | None, typer.Option(help="Or a currency vs TRY")] = None,
    param: Annotated[list[str] | None, typer.Option(help="key=JSON (repeatable)")] = None,
) -> None:
    """Register a strategy (status: research)."""
    params = {k: json.loads(v) for k, _, v in (p.partition("=") for p in param or [])}
    try:
        with _db() as db:
            deploy(
                db,
                name,
                signal=signal,
                params=params,
                costs=costs,
                capital=Decimal(capital),
                instrument=instrument,
                fx=fx,
            )
    except (QPError, ValueError, json.JSONDecodeError) as exc:
        fail(str(exc))
    console.print(
        f"{name} deployed (research). Next: qp trader promote {name} backtested "
        "--evidence <walk-forward run folder>"
    )


@app.command("promote")
def promote_cmd(
    name: Annotated[str, typer.Argument(help="Strategy name")],
    status: Annotated[str, typer.Argument(help="backtested | paper | retired | approved_live")],
    evidence: Annotated[
        str, typer.Option(help="For backtested: a walk-forward result folder")
    ] = "",
    note: Annotated[str, typer.Option(help="Why")] = "",
) -> None:
    """Move a strategy along research -> backtested -> paper (-> approved_live: owner only)."""
    confirmed = False
    if status == "approved_live":
        if not sys.stdin.isatty():
            fail("approved_live needs the owner at an interactive terminal")
        phrase = f"APPROVE LIVE {name}"
        typed = typer.prompt(f"Type '{phrase}' to approve live trading for {name}")
        if typed.strip() != phrase:
            fail("confirmation did not match; nothing recorded")
        confirmed = True
    try:
        with _db() as db:
            promote(db, name, status, evidence=evidence, note=note, owner_confirmed=confirmed)
    except QPError as exc:
        fail(str(exc))
    console.print(f"{name}: {status}")


@app.command("status")
def status_cmd() -> None:
    """Strategies, their status and kill-switch state."""
    try:
        with _db() as db:
            rows = [(d, gate.halted(db, d.name)) for d in deployments(db)]
            global_halt = gate.halted(db, "-")
    except QPError as exc:
        fail(str(exc))
    if global_halt:
        console.print(f"[red]GLOBAL HALT[/red] {global_halt}")
    t = Table("name", "signal", "market", "status", "capital", "halted")
    for d, h in rows:
        t.add_row(
            d.name,
            f"{d.signal} {d.params}",
            d.instrument or f"{d.fx}/TRY",
            d.status or "—",
            money(d.capital),
            f"[red]{h}[/red]" if h else "no",
        )
    console.print(t)


@app.command("run")
def run_cmd() -> None:
    """One paper cycle for every strategy in paper status (idempotent per bar)."""
    cfg = settings()
    try:
        with Database.from_settings(cfg.db) as db:
            reports = run_paper(cfg, db, now=SystemClock().now())
    except QPError as exc:
        fail(str(exc))
    for r in reports:
        state = f"[red]halted: {r.halted}[/red]" if r.halted else "ok"
        console.print(
            f"{r.strategy}: bars {', '.join(map(str, r.processed)) or 'none new'}; "
            f"orders {r.orders}; reconciled {r.reconciled}; {state}"
        )
        for x in r.rejected:
            console.print(f"  [yellow]rejected[/yellow] {x}")
        for n in r.notes:
            console.print(f"  {n}")
    if not reports:
        console.print("no strategy in paper status")


@app.command("halt")
def halt_cmd(
    scope: Annotated[str, typer.Argument(help="A strategy name, or 'global'")],
    reason: Annotated[str, typer.Option(help="Why")],
) -> None:
    """Kill switch: no new orders; pending targets are dropped."""
    with _db() as db:
        gate.set_halt(db, scope, halted_=True, reason=reason)
    console.print(f"[red]halted[/red] {scope}: {reason}")


@app.command("resume")
def resume_cmd(
    scope: Annotated[str, typer.Argument(help="A strategy name, or 'global'")],
    reason: Annotated[str, typer.Option(help="Why it is safe again")],
) -> None:
    with _db() as db:
        gate.set_halt(db, scope, halted_=False, reason=reason)
    console.print(f"resumed {scope}")


@app.command("flatten")
def flatten_cmd(name: Annotated[str, typer.Argument(help="Strategy name")]) -> None:
    """Close the position at the next bar (even while halted), then halt the strategy."""
    with _db() as db:
        gate.set_halt(db, f"flatten:{name}", halted_=True, reason="flatten requested")
    console.print(f"{name}: flatten at the next bar")


@app.command("report")
def report_cmd(name: Annotated[str, typer.Argument(help="Strategy name")]) -> None:
    """Paper equity vs the reference backtest over the same bars."""
    cfg = settings()
    try:
        with Database.from_settings(cfg.db) as db:
            r = paper_vs_backtest(cfg, db, name, now=SystemClock().now())
    except QPError as exc:
        fail(str(exc))
    t = Table(
        "day",
        "paper",
        "backtest",
        "difference",
        title=f"{name}: paper vs backtest ({r.weeks:.1f} weeks)",
    )
    for d in r.days[-30:]:
        t.add_row(str(d.day), money(d.paper), money(d.backtest), money(d.diff))
    console.print(t)
    console.print(f"max |difference| {money(r.max_abs_diff)}; exit criterion needs 4+ weeks")
    for x in r.rejections:
        console.print(f"[yellow]rejected[/yellow] {x}")
    for n in r.notes:
        console.print(n)
