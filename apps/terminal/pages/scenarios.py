"""Scenarios page: shocks, historical replays and worst observed losses on today's holdings
(ADR 0016). Read-only; scenario definitions live in configs/scenarios.yaml."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from apps.terminal import charts
from apps.terminal.state import context_or_stop, reload_button
from qp.core import SystemClock, istanbul_date
from qp.core.errors import QPError
from qp.risk.service import asset_classes
from qp.scenarios.service import scenario_report


def num(x: Decimal | None) -> str:
    return "—" if x is None else f"{x:,.2f}"


def pct(x: Decimal | None) -> str:
    return "—" if x is None else f"{x:.2%}"


st.title("Scenarios")
reload_button()
ctx = context_or_stop()
day = st.date_input("As of", value=istanbul_date(SystemClock().now()))
try:
    classes = asset_classes(ctx)
except Exception:  # the instrument master is optional for this page
    classes = {}
try:
    rep = scenario_report(ctx, day=day, classes=classes)
except (QPError, ValueError) as exc:
    st.error(str(exc))
    st.stop()

st.caption(
    f"Holdings on {day}: {num(rep.value_try)} TRY. Shock sizes and windows are assumptions "
    f"in {ctx.settings.scenarios.file}; results come from your holdings and stored data."
)
for n in rep.notes:
    st.caption(f"Note: {n}")
ok = [r for r in rep.results if not r.unavailable]
if ok:
    charts.show(
        charts.signed_hbars("Scenario P&L (TRY)", [r.name for r in ok], [float(r.pnl) for r in ok])
    )
st.dataframe(
    [
        {
            "scenario": r.name,
            "kind": r.kind,
            "window": f"{r.window[0]} → {r.window[1]}" if r.window else "",
            "P&L (TRY)": "—" if r.unavailable else num(r.pnl),
            "change": "—" if r.unavailable else pct(r.change),
            "covered": pct(r.covered_try / rep.value_try) if rep.value_try else "—",
            "status": r.unavailable or (f"{len(r.missing)} left out" if r.missing else "ok"),
        }
        for r in rep.results
    ],
    hide_index=True,
    width="stretch",
)
for r in rep.results:
    if r.unavailable:
        continue
    with st.expander(r.name):
        if r.description:
            st.caption(r.description)
        st.dataframe(
            [
                {
                    "holding": row.name,
                    "value (TRY)": num(row.value_try),
                    "change": pct(row.change),
                    "P&L (TRY)": num(row.pnl),
                    "how": row.note,
                }
                for row in r.rows
            ],
            hide_index=True,
            width="stretch",
        )
        for n in r.notes:
            st.caption(n)
