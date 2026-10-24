"""``qp value``: point-in-time fundamentals, multiples, DCF and reverse DCF (ADR 0013)."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, pct, settings
from qp.core import SystemClock, parse_utc
from qp.core.errors import QPError
from qp.data.lake import CleanStore
from qp.fundamentals.facts import Snapshot, snapshot, ticker_ciks
from qp.platform.lake import LakePaths
from qp.valuation.dcf import dcf, multiples, reverse_dcf

app = typer.Typer(no_args_is_help=True, help="Valuation: fundamentals, multiples, DCF.")


def _snapshot(ticker: str, known_at: str | None) -> Snapshot:
    cfg = settings()
    cik = ticker_ciks(cfg.lake.root).get(ticker.upper())
    if cik is None:
        fail(f"{ticker}: unknown; run `uv run qp data fetch sec --series {ticker}` first")
    as_of = parse_utc(known_at) if known_at else SystemClock().now()
    return snapshot(CleanStore(LakePaths(cfg.lake.root)), cik, as_of=as_of)


@app.command("fundamentals")
def fundamentals(
    ticker: Annotated[str, typer.Argument(help="US ticker (SEC)")],
    known_at: Annotated[str | None, typer.Option(help="As known at this ISO time")] = None,
) -> None:
    """TTM and latest balance-sheet values as filed by a moment."""
    s = _snapshot(ticker, known_at)
    t = Table(
        "item",
        "value",
        "period end",
        "how",
        title=f"{ticker} as known at {s.as_of:%Y-%m-%d %H:%M} UTC",
    )
    for name in (
        "revenue",
        "net_income",
        "operating_income",
        "d_and_a",
        "cfo",
        "capex",
        "equity",
        "cash",
        "debt_long",
        "debt_short",
        "shares",
    ):
        v = getattr(s, name)
        t.add_row(
            name, f"{v.value:,.0f}" if v else "—", str(v.end) if v else "—", v.basis if v else ""
        )
    console.print(t)
    for n in s.notes:
        console.print(f"[yellow]note:[/yellow] {n}")


@app.command("multiples")
def multiples_cmd(
    ticker: Annotated[str, typer.Argument(help="US ticker (SEC)")],
    price: Annotated[float, typer.Option(help="Share price in USD")],
    known_at: Annotated[str | None, typer.Option(help="As known at this ISO time")] = None,
) -> None:
    """P/E, P/B, EV/EBITDA, EV/Sales and FCF yield from what was filed by a moment."""
    s = _snapshot(ticker, known_at)
    try:
        m = multiples(s, price)
    except QPError as exc:
        fail(str(exc))
    t = Table("multiple", "value", title=f"{ticker} at {price}")
    t.add_row("market cap", f"{m.market_cap:,.0f}")
    t.add_row(
        "enterprise value", "—" if m.enterprise_value is None else f"{m.enterprise_value:,.0f}"
    )
    for label, x in (
        ("P/E", m.pe),
        ("P/B", m.pb),
        ("EV/EBITDA", m.ev_ebitda),
        ("EV/Sales", m.ev_sales),
    ):
        t.add_row(label, "—" if x is None else f"{x:.2f}")
    t.add_row("FCF yield", pct(m.fcf_yield))
    console.print(t)
    if m.missing:
        console.print(f"missing inputs: {', '.join(m.missing)}")


@app.command("dcf")
def dcf_cmd(
    fcff: Annotated[str, typer.Option(help="Comma-separated yearly FCFF forecasts")],
    rate: Annotated[float, typer.Option(help="Discount rate (WACC), e.g. 0.09")],
    growth: Annotated[float, typer.Option(help="Terminal growth, e.g. 0.02")],
    net_debt: Annotated[float, typer.Option(help="Net debt")] = 0.0,
    shares: Annotated[float | None, typer.Option(help="Shares outstanding")] = None,
    market_ev: Annotated[float | None, typer.Option(help="Also solve the implied growth")] = None,
    base_fcff: Annotated[
        float | None, typer.Option(help="With --market-ev: latest actual FCFF (year 0)")
    ] = None,
) -> None:
    """DCF of explicit forecasts; with --market-ev and --base-fcff, the constant growth from
    the base over the same number of years that the market EV implies."""
    flows = [float(x) for x in fcff.split(",")]
    if market_ev is not None and base_fcff is None:
        fail("--market-ev needs --base-fcff (the latest actual FCFF)")
    try:
        r = dcf(flows, discount_rate=rate, terminal_growth=growth, net_debt=net_debt, shares=shares)
        implied = (
            reverse_dcf(
                market_ev,
                base_fcff=base_fcff,
                years=len(flows),
                discount_rate=rate,
                terminal_growth=growth,
            )
            if market_ev is not None and base_fcff is not None
            else None
        )
    except QPError as exc:
        fail(str(exc))
    console.print(
        f"EV {r.enterprise_value:,.2f} (explicit {r.pv_explicit:,.2f}, "
        f"terminal {r.pv_terminal:,.2f}, {pct(r.terminal_share)} of EV)"
    )
    console.print(
        f"equity {r.equity_value:,.2f}" + (f", per share {r.per_share:,.2f}" if r.per_share else "")
    )
    if implied is not None:
        console.print(
            f"implied FCFF growth for {len(flows)} years from {base_fcff:,.2f}: {pct(implied)}"
        )
