"""Overview page: net worth, the latest day, year to date, allocation, alerts and data
freshness. Read-only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st

from apps.terminal import charts
from apps.terminal.state import context_or_stop, reload_button
from qp.alerts.store import recent
from qp.core import SystemClock, istanbul_date, to_istanbul
from qp.data import service as data_service
from qp.data.market import PreloadedMarketData
from qp.performance.history import LATEST
from qp.performance.metrics import Summary, View
from qp.performance.service import performance_report
from qp.platform import Database
from qp.portfolio.holdings import build_report
from qp.risk.exposure import exposures
from qp.risk.service import asset_classes, holdings_of

st.title("Overview")
reload_button()
ctx = context_or_stop()
today = istanbul_date(SystemClock().now())
report = performance_report(ctx, end=today, view=View.TRY, with_periods=True)
s = report.summary


def pct(x: Decimal | float | None) -> str:
    return "—" if x is None else f"{float(x):.2%}"


if isinstance(s, Summary):
    last_day, last_r = s.returns[-1] if s.returns else (s.end, Decimal(0))
    ytd = report.periods.get("YTD")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Net worth (TRY)", f"{s.end_value:,.2f}", delta=f"{last_r:.2%} on {last_day}")
    c2.metric("YTD TWR", pct(ytd.twr) if isinstance(ytd, Summary) else "—")
    c3.metric("Since start TWR", pct(s.twr), help=f"from {s.start}")
    c4.metric("Drawdown from peak", pct(s.current_drawdown))
    charts.show(
        charts.lines(
            "Net worth (TRY)",
            [("Net worth", [p.day for p in s.values], [float(p.value) for p in s.values])],
        )
    )
else:
    st.warning(f"Performance unavailable: {s.reason}")

try:
    market = PreloadedMarketData(ctx.market, as_of=LATEST, until=today)
    holdings_report = build_report(ctx.transactions, market, as_of=today)
    try:
        classes = asset_classes(ctx)
    except Exception:
        classes = {}
    expo = exposures(
        holdings_of(holdings_report),
        lambda h: "cash" if h.instrument is None else classes.get(h.instrument, "unknown"),
    )
    a, b = st.columns(2)
    with a:
        if expo.by_asset_class:
            charts.show(
                charts.hbars(
                    "Allocation by asset class",
                    [x.name for x in expo.by_asset_class],
                    [float(x.weight) for x in expo.by_asset_class],
                )
            )
    with b:
        if expo.by_currency:
            charts.show(
                charts.hbars(
                    "Allocation by currency",
                    [x.name for x in expo.by_currency],
                    [float(x.weight) for x in expo.by_currency],
                )
            )
    if not holdings_report.complete:
        st.warning("Some holdings have no price or rate today; totals leave them out.")
except Exception as exc:  # the rest of the page still works
    st.info(f"Allocation unavailable: {type(exc).__name__}")

st.subheader("Recent alerts")
try:
    with Database.from_settings(ctx.settings.db) as db:
        events = recent(db, 10)
    if events:
        st.dataframe(
            [
                {
                    "created (Istanbul)": f"{to_istanbul(e.created_at):%Y-%m-%d %H:%M}",
                    "level": e.level,
                    "message": e.message,
                    "sent": "yes" if e.sent_at else (e.delivery_error or "no"),
                }
                for e in events
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("No alerts yet (`uv run qp alerts run`, or the workers job at 18:30).")
except Exception as exc:
    st.caption(f"Alerts need PostgreSQL: {type(exc).__name__}")

st.subheader("Data freshness")
st.dataframe(
    [
        {
            "dataset": d.name,
            "last valid date": str(d.last_valid or "—"),
            "days old": (today - d.last_valid).days if isinstance(d.last_valid, date) else None,
        }
        for d in data_service.dataset_status(ctx.settings)
    ],
    hide_index=True,
    width="stretch",
)
