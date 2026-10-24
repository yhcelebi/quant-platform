"""Performance page: TWR, money-weighted return, benchmarks, drawdowns, monthly returns and
P&L attribution (ADR 0010). Read-only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st

from apps.terminal import charts
from apps.terminal.state import context_or_stop, reload_button
from qp.core import SystemClock, istanbul_date
from qp.performance.metrics import VIEW_LABEL, Summary, Unavailable, View
from qp.performance.service import (
    attribution_report,
    performance_report,
    standard_periods,
)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def pct(x: Decimal | float | None) -> str:
    return "—" if x is None or x != x else f"{float(x):.2%}"


def num(x: Decimal | None) -> str:
    return "—" if x is None else f"{x:,.2f}"


st.title("Performance")
reload_button()
ctx = context_or_stop()
today = istanbul_date(SystemClock().now())

c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
end = c1.date_input("End", value=today)
period = c2.selectbox("Period", ["Since start", "YTD", "1Y", "3Y", "3M", "1M", "Custom"])
view = View(
    c3.radio(
        "View", [v.value for v in View], horizontal=True, format_func=lambda v: VIEW_LABEL[View(v)]
    )
)
codes = [a.code for a in ctx.accounts]
chosen = c4.multiselect("Accounts", codes, default=codes)
start: date | None = standard_periods(end).get(period)
if period == "Custom":
    start = st.date_input("Start (close of this day)", value=date(end.year - 1, 12, 31))

report = performance_report(
    ctx,
    end=end,
    start=start,
    view=view,
    accounts=None if set(chosen) == set(codes) else chosen,
    with_periods=True,
)
unit = VIEW_LABEL[view]
s = report.summary
if isinstance(s, Unavailable):
    st.warning(f"Performance unavailable: {s.reason}")
    if report.inflation_until:
        st.caption(f"Inflation index available to {report.inflation_until}.")
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric(f"TWR {s.start} → {s.end}", pct(s.twr))
if s.days >= 365:
    m2.metric("TWR annualized", pct(s.twr_annualized), help=f"XIRR {pct(s.xirr)}")
else:
    m2.metric("Money-weighted (period)", pct(s.mwr_period), help="Not annualized (< 1 year)")
m3.metric(f"P&L ({unit})", num(s.pnl), help=f"Net external flows {num(s.net_flows)}")
m4.metric(
    "Max drawdown",
    pct(s.max_drawdown.depth) if s.max_drawdown else "0.00%",
    help=f"Current drawdown {pct(s.current_drawdown)}; volatility {pct(s.volatility)}",
)

# float conversion for charts only; the numbers above and in tables are exact Decimals
series = [("Portfolio", [d for d, _ in s.wealth], [float(w - 1) for _, w in s.wealth])]
missing: list[str] = []
for spec, r in report.benchmarks:
    if isinstance(r, Unavailable):
        missing.append(f"{spec.name}: {r.reason}")
    else:
        series.append((spec.name, [d for d, _ in r.levels], [float(x - 1) for _, x in r.levels]))
charts.show(
    charts.lines(f"Cumulative return ({unit}), portfolio vs benchmarks", series, y_format=".1%")
)
if missing:
    st.caption("Benchmarks without data: " + "; ".join(missing))

left, right = st.columns(2)
with left:
    charts.show(
        charts.lines(
            f"Value ({unit})",
            [("Value", [p.day for p in s.values], [float(p.value) for p in s.values])],
        )
    )
with right:
    peak = Decimal(0)
    dd: list[float] = []
    for _, w in s.wealth:
        peak = max(peak, w)
        dd.append(float(w / peak - 1))
    charts.show(charts.drawdown("Drawdown from peak (TWR index)", [d for d, _ in s.wealth], dd))

if report.monthly:
    years = sorted({y for y, _ in report.monthly})
    grid: list[list[float | None]] = []
    for y in years:
        row = [report.monthly.get((y, m)) for m in range(1, 13)]
        grid.append([None if v is None else float(v) for v in row])
    limit = max((abs(v) for row in grid for v in row if v is not None), default=0.01) or 0.01
    charts.show(
        charts.diverging_heatmap(
            f"Monthly returns ({unit})",
            [str(y) for y in years],
            MONTHS,
            grid,
            limit=limit,
            fmt=".1%",
        )
    )

st.subheader("Periods and benchmarks")
pc, bc = st.columns(2)
pc.dataframe(
    [
        {
            "period": label,
            "start": str(p.start) if isinstance(p, Summary) else "—",
            "TWR": pct(p.twr) if isinstance(p, Summary) else "—",
            "annualized": pct(p.twr_annualized) if isinstance(p, Summary) else "—",
            "note": "" if isinstance(p, Summary) else p.reason,
        }
        for label, p in report.periods.items()
    ],
    hide_index=True,
    width="stretch",
)
bc.dataframe(
    [
        {
            "benchmark": spec.name,
            "return": "—" if isinstance(r, Unavailable) else pct(r.total),
            "portfolio minus benchmark": "—"
            if isinstance(r, Unavailable)
            else pct(s.twr - r.total),
        }
        for spec, r in report.benchmarks
    ],
    hide_index=True,
    width="stretch",
)
with st.expander("Monthly and yearly returns (table)"):
    st.dataframe(
        [{"period": f"{y}-{m:02d}", "TWR": pct(r)} for (y, m), r in sorted(report.monthly.items())]
        + [{"period": str(y), "TWR": pct(r)} for (y, _), r in sorted(report.yearly.items())],
        hide_index=True,
    )

st.subheader(f"P&L attribution {s.start} → {s.end} (TRY)")
attr = attribution_report(
    ctx, start=s.start, end=s.end, accounts=None if set(chosen) == set(codes) else chosen
)
if isinstance(attr, Unavailable):
    st.info(f"Attribution unavailable: {attr.reason}")
else:
    st.dataframe(
        [
            {
                "bucket": r.label,
                "start": num(r.start_value),
                "end": num(r.end_value),
                "inflow": num(r.inflow),
                "P&L": num(r.pnl),
                "price effect": num(r.price_effect),
                "FX effect": num(r.fx_effect),
            }
            for r in attr.rows
        ]
        + [
            {
                "bucket": "total",
                "start": num(attr.start_value),
                "end": num(attr.end_value),
                "inflow": num(attr.external_flows),
                "P&L": num(attr.total_pnl),
            }
        ],
        hide_index=True,
        width="stretch",
    )
for note in s.notes:
    st.caption(note)
if s.stale:
    with st.expander(f"Stale values used ({len(s.stale)})"):
        for x in s.stale:
            st.write(f"- {x}")
