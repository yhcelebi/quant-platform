"""``qp backtest``: NautilusTrader backtests and walk-forward runs (ADR 0014)."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any

import typer
from rich.table import Table

from apps.cli.common import console, fail, pct, settings
from qp.backtest.reference import simulate
from qp.backtest.results import list_runs, read_summary
from qp.backtest.service import cost_model, load_series, run, run_walk_forward
from qp.core import SystemClock, parse_utc
from qp.core.errors import QPError

app = typer.Typer(no_args_is_help=True, help="Backtests (NautilusTrader) and walk-forward.")

Instrument = Annotated[str | None, typer.Option(help="SYMBOL.VENUE with prices in the data")]
Fx = Annotated[str | None, typer.Option(help="Or a currency traded against TRY (TCMB rates)")]
Signal = Annotated[str, typer.Option(help="sma_cross | momentum | buy_and_hold")]
Costs = Annotated[str, typer.Option(help="Cost profile (backtest.costs) or 'costless'")]
Start = Annotated[str | None, typer.Option(help="First day YYYY-MM-DD")]
End = Annotated[str | None, typer.Option(help="Last day YYYY-MM-DD")]
KnownAt = Annotated[str | None, typer.Option(help="Data as known at this ISO time (default: now)")]


def _params(items: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        k, _, v = item.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _metrics_table(title: str, m: dict[str, Any]) -> Table:
    t = Table("metric", "value", title=title)
    t.add_row("total return", pct(m["total_return"]))
    t.add_row("CAGR", pct(m["cagr"]) if m["cagr"] is not None else "— (< 1 year)")
    t.add_row("volatility", pct(m["volatility"]))
    t.add_row("Sharpe", "—" if m["sharpe"] is None else f"{m['sharpe']:.2f}")
    t.add_row("max drawdown", pct(m["max_drawdown"]))
    t.add_row("trades", str(m["trades"]))
    t.add_row("fees", f"{m['fees']:,.2f}")
    t.add_row("time in market", pct(m["exposure"]))
    return t


@app.command("run")
def run_cmd(
    signal: Signal,
    costs: Costs,
    instrument: Instrument = None,
    fx: Fx = None,
    param: Annotated[list[str] | None, typer.Option(help="key=value (repeatable)")] = None,
    start: Start = None,
    end: End = None,
    known_at: KnownAt = None,
) -> None:
    """One backtest; stored with provenance under data/lake/results/backtests/."""
    cfg = settings()
    try:
        series = load_series(
            cfg,
            instrument=instrument,
            fx=fx,
            as_of=parse_utc(known_at) if known_at else SystemClock().now(),
            start=date.fromisoformat(start) if start else None,
            end=date.fromisoformat(end) if end else None,
        )
        _, folder = run(cfg, series=series, signal=signal, params=_params(param), costs=costs)
        baseline = simulate(
            series,
            signal="buy_and_hold",
            params={},
            capital=cfg.backtest.capital,
            costs=cost_model(cfg, costs),
            lag=cfg.backtest.lag_bars,
        )
    except QPError as exc:
        fail(str(exc))
    summary = read_summary(folder)
    console.print(
        _metrics_table(
            f"{signal} on {series.instrument} {series.days[0]}..{series.days[-1]} "
            f"({len(series.days)} bars)",
            summary["metrics"],
        )
    )
    console.print(
        "buy and hold over the same bars, same costs: "
        f"{pct(float(baseline.equity[-1] / baseline.equity[0] - 1))}"
    )
    if not summary["valid"]:
        console.print(f"[red]INVALID:[/red] {summary['invalid_reason']}")
    console.print(f"stored {folder}")


@app.command("walkforward")
def walkforward_cmd(
    signal: Signal,
    costs: Costs,
    instrument: Instrument = None,
    fx: Fx = None,
    train: Annotated[int, typer.Option(help="Training bars per window")] = 252,
    test: Annotated[int, typer.Option(help="Test bars per window")] = 63,
    start: Start = None,
    end: End = None,
    known_at: KnownAt = None,
) -> None:
    """Rolling walk-forward over the signal's parameter grid; reports DSR and PBO."""
    cfg = settings()
    try:
        series = load_series(
            cfg,
            instrument=instrument,
            fx=fx,
            as_of=parse_utc(known_at) if known_at else SystemClock().now(),
            start=date.fromisoformat(start) if start else None,
            end=date.fromisoformat(end) if end else None,
        )
        wf, folder = run_walk_forward(
            cfg, series=series, signal=signal, costs=costs, train=train, test=test
        )
    except QPError as exc:
        fail(str(exc))
    t = Table(
        "train",
        "test",
        "chosen",
        "train Sharpe",
        "test return",
        title=f"Walk-forward {signal} on {series.instrument}",
    )
    for w in wf.windows:
        t.add_row(
            f"{w.train[0]}..{w.train[1]}",
            f"{w.test[0]}..{w.test[1]}",
            json.dumps(w.params),
            "—" if w.train_sharpe is None else f"{w.train_sharpe:.2f}",
            pct(w.test_return),
        )
    console.print(t)
    s = read_summary(folder)
    console.print(_metrics_table("Out of sample (chained)", s["metrics"]))
    console.print(
        f"grid {len(wf.grid)} trials; PSR {pct(wf.psr)}, deflated Sharpe {pct(wf.dsr)}, "
        f"PBO {pct(wf.pbo)}"
    )
    for n in wf.notes:
        console.print(f"note: {n}")
    console.print(f"stored {folder}")


@app.command("list")
def list_cmd(limit: Annotated[int, typer.Option(help="Rows")] = 20) -> None:
    """Stored backtest runs, newest last."""
    t = Table("run", "kind", "signal", "instrument", "valid", "total return", "Sharpe")
    for folder in list_runs(settings())[-limit:]:
        s = read_summary(folder)
        m = s["metrics"] or {}
        t.add_row(
            f"{folder.parent.name}/{folder.name}",
            s["kind"],
            s["spec"]["signal"],
            s["spec"]["instrument"],
            "yes" if s["valid"] else "[red]no[/red]",
            pct(m.get("total_return")),
            "—" if m.get("sharpe") is None else f"{m['sharpe']:.2f}",
        )
    console.print(t)
