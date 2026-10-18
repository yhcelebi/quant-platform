"""Portfolio page: holdings, cash and P&L at any date. Read-only."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from qp.core import SystemClock, istanbul_date
from qp.core.errors import QPError
from qp.platform import init_platform
from qp.portfolio.holdings import CostMethod, HoldingsReport
from qp.portfolio.service import load_report


def fmt(value: Decimal | None, places: int = 2) -> str:
    """Display only; stored numbers stay exact."""
    return "—" if value is None else f"{value:,.{places}f}"


def view(value_try: Decimal | None, report: HoldingsReport, usd: bool) -> Decimal | None:
    if value_try is None:
        return None
    if not usd:
        return value_try
    return value_try / report.usd_try.rate_try if report.usd_try else None


st.title("Portfolio")
try:
    settings = init_platform()
except QPError as exc:
    st.error(f"Configuration error: {exc}")
    st.stop()

left, mid, right = st.columns(3)
as_of = left.date_input("As of", value=istanbul_date(SystemClock().now()))
currency = mid.radio("Values in", ["TRY", "USD"], horizontal=True)
method = right.radio("Cost basis", [m.value for m in CostMethod], horizontal=True)

try:
    report, accounts = load_report(settings, as_of=as_of, method=CostMethod(method))
except Exception as exc:  # show the reason instead of a stack trace
    st.error(f"Could not load the ledger: {type(exc).__name__}: {str(exc).splitlines()[0]}")
    st.caption("Is PostgreSQL running (`make db-up`) and migrated (`make migrate`)?")
    st.stop()

codes = [a.code for a in accounts]
chosen = st.multiselect("Accounts", codes, default=codes)
if set(chosen) != set(codes):
    report, _ = load_report(settings, as_of=as_of, method=CostMethod(method), accounts=chosen)

usd = currency == "USD"
if usd and report.usd_try is None:
    st.warning("No USD/TRY rate for this date; showing TRY.")
    usd = False
unit = "USD" if usd else "TRY"

total = report.total_usd if usd else report.total_try
c1, c2, c3 = st.columns(3)
c1.metric(
    f"Total ({unit})",
    fmt(total),
    help=None if report.complete else "Incomplete: some values missing",
)
c2.metric(f"Unrealized ({unit})", fmt(view(report.unrealized_try, report, usd)))
realized_try = sum((r.realized_try for r in report.realized), Decimal(0))
c3.metric("Realized YTD (TRY)", fmt(realized_try))
if not report.complete:
    st.warning(
        "Total is incomplete: at least one position or cash balance has no price or FX rate."
    )

rows = [
    {
        "account": r.valuation.account,
        "instrument": str(r.valuation.instrument),
        "quantity": f"{r.valuation.quantity.normalize():f}",
        "avg cost": f"{fmt(r.valuation.avg_cost, 4)} {r.valuation.currency}",
        "price": fmt(r.valuation.price, 4),
        "price date": f"{r.price.valid_date}{' (stale)' if r.stale else ''}" if r.price else "—",
        f"value {unit}": fmt(view(r.valuation.value_try, report, usd)),
        f"unrealized {unit}": fmt(view(r.valuation.unrealized_try, report, usd)),
        "FX effect TRY": fmt(r.valuation.fx_effect_try),
        "price effect TRY": fmt(r.valuation.price_effect_try),
    }
    for r in report.positions
]
st.subheader("Positions")
st.dataframe(rows, hide_index=True, width="stretch")

st.subheader("Cash")
st.dataframe(
    (
        [
            {
                "account": c.account,
                "currency": c.currency.code,
                "amount": fmt(c.amount),
                f"value {unit}": fmt(view(c.amount_try, report, usd)),
            }
            for c in report.cash
        ]
    ),
    hide_index=True,
    width="stretch",
)

if report.usd_try:
    fx = report.usd_try
    st.caption(f"USD/TRY {fx.rate_try} on {fx.valid_date} ({fx.source})")
if report.warnings:
    with st.expander(f"Warnings ({len(report.warnings)})", expanded=True):
        for w in report.warnings:
            st.write(f"- {w}")
