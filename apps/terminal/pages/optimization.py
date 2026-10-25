"""Optimization page: model weights for today's holdings (ADR 0016). Read-only; a model
output, never an order."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from apps.terminal import charts
from apps.terminal.state import context_or_stop, reload_button
from qp.core import SystemClock, istanbul_date
from qp.core.errors import QPError
from qp.optimization.methods import Method
from qp.optimization.service import optimize

LABELS = {
    Method.HRP: "Hierarchical risk parity",
    Method.RISK_PARITY: "Risk parity (equal risk contribution)",
    Method.MIN_VARIANCE: "Minimum variance",
    Method.MIN_CVAR: "Minimum CVaR",
    Method.MAX_SHARPE: "Maximum Sharpe (historical means)",
}


def num(x: Decimal | None) -> str:
    return "—" if x is None else f"{x:,.2f}"


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.2%}"


st.title("Optimization")
reload_button()
ctx = context_or_stop()
c1, c2, c3 = st.columns([1, 2, 1])
day = c1.date_input("As of", value=istanbul_date(SystemClock().now()))
method = c2.selectbox("Model", list(LABELS), format_func=LABELS.__getitem__)
cap = c3.number_input(
    "Max weight per asset", 0.05, 1.0, float(ctx.settings.optimization.cap), step=0.05
)
extra_text = st.text_input(
    "Also consider (not held)",
    "",
    help="Comma-separated: currency codes (USD, EUR) or SYMBOL.VENUE",
)
extra = [x.strip() for x in extra_text.split(",") if x.strip()]
try:
    r = optimize(ctx, day=day, method=method, extra=extra, cap=cap)
except (QPError, ValueError) as exc:
    st.error(str(exc))
    st.stop()

for e in r.excluded:
    st.caption(f"Left out: {e}")
if r.unavailable:
    st.info(f"Unavailable: {r.unavailable}")
    st.stop()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Volatility, model", pct(r.volatility))
k2.metric("Volatility, current", pct(r.current_volatility))
k3.metric(f"Daily CVaR {r.cvar_level:.0%}, model", pct(r.cvar))
k4.metric(f"Daily CVaR {r.cvar_level:.0%}, current", pct(r.current_cvar))
st.caption(
    f"{r.observations} days of daily TRY returns from {r.first_day} to {day}; "
    f"invested value {num(r.invested_try)} TRY (TRY cash excluded)."
)
names = [a.name for a in r.assets]
held = r.invested_try > 0
series = [("Current", [a.current for a in r.assets])] if held else []
series.append(("Model", [a.target for a in r.assets]))
charts.show(charts.grouped_hbars("Weights", names, series))
st.dataframe(
    [
        {
            "asset": a.name,
            "value (TRY)": num(a.value_try),
            "current": pct(a.current),
            "model": pct(a.target),
            "risk share": pct(a.risk_share),
            "trade (TRY)": num(a.trade_try),
        }
        for a in r.assets
    ],
    hide_index=True,
    width="stretch",
)
for n in r.notes:
    st.caption(f"Note: {n}")
st.caption("Model output under the stated assumptions. Nothing here places an order.")
