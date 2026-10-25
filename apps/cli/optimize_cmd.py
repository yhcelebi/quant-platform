"""``qp optimize``: weight models for today's holdings (ADR 0016). Model output, no orders."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, money, pct, settings
from qp.core import SystemClock, istanbul_date, parse_utc
from qp.core.errors import QPError
from qp.optimization.methods import Method
from qp.optimization.service import optimize
from qp.performance.service import load_context

app = typer.Typer(no_args_is_help=True, help="Optimization: model weights (no orders).")


@app.command("run")
def run(
    method: Annotated[Method, typer.Option(help="Weight model")] = Method.HRP,
    as_of: Annotated[str | None, typer.Option(help="Day YYYY-MM-DD (default: today)")] = None,
    account: Annotated[list[str] | None, typer.Option(help="Only these accounts")] = None,
    add: Annotated[
        list[str] | None, typer.Option(help="Asset not held: currency (USD) or SYMBOL.VENUE")
    ] = None,
    cap: Annotated[float | None, typer.Option(help="Per-asset maximum weight")] = None,
    known_at: Annotated[str | None, typer.Option(help="As known at this ISO time")] = None,
) -> None:
    """Model weights for the holdings in scope versus their current weights."""
    day = date.fromisoformat(as_of) if as_of else istanbul_date(SystemClock().now())
    try:
        r = optimize(
            load_context(settings()),
            day=day,
            method=method,
            accounts=account,
            known_at=parse_utc(known_at) if known_at else None,
            extra=add or (),
            cap=cap,
        )
    except (QPError, ValueError) as exc:
        fail(str(exc))
    for e in r.excluded:
        console.print(f"[yellow]left out:[/yellow] {e}")
    if r.unavailable:
        fail(f"{method.value}: unavailable — {r.unavailable}")
    t = Table(
        "asset",
        "value (TRY)",
        "current",
        "model",
        "risk share",
        "trade (TRY)",
        title=f"{method.value}: {r.observations} days from {r.first_day} to {day}",
    )
    for a in r.assets:
        t.add_row(
            a.name,
            money(a.value_try),
            pct(a.current),
            pct(a.target),
            pct(a.risk_share),
            money(a.trade_try),
        )
    console.print(t)
    console.print(
        f"volatility (annualized): model {pct(r.volatility)}, current {pct(r.current_volatility)}"
    )
    console.print(
        f"daily CVaR {pct(r.cvar_level, 0)}: model {pct(r.cvar)}, current {pct(r.current_cvar)}"
    )
    for n in r.notes:
        console.print(f"[yellow]note:[/yellow] {n}")
    console.print("Model output under the stated assumptions; no order is placed.")
