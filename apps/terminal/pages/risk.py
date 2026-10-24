"""Risk page: one-day VaR/ES, scenario distribution, exposures, concentration and
correlation (ADR 0010). Read-only."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from apps.terminal import charts
from apps.terminal.state import context_or_stop, reload_button
from qp.core import SystemClock, istanbul_date
from qp.risk.service import asset_classes, risk_report
from qp.risk.var import Method, VarResult


def num(x: Decimal | None) -> str:
    return "—" if x is None else f"{x:,.2f}"


st.title("Risk")
reload_button()
ctx = context_or_stop()
c1, c2 = st.columns([1, 3])
day = c1.date_input("As of", value=istanbul_date(SystemClock().now()))
codes = [a.code for a in ctx.accounts]
chosen = c2.multiselect("Accounts", codes, default=codes)
try:
    classes = asset_classes(ctx)
except Exception:  # the instrument master is optional for this page
    classes = {}
report = risk_report(
    ctx, day=day, accounts=None if set(chosen) == set(codes) else chosen, classes=classes
)


def var_block(r: VarResult) -> None:
    st.markdown(f"**{r.source}** — {r.observations} days, value {num(r.value)} TRY")
    if r.unavailable:
        st.info(f"Unavailable: {r.unavailable}")
    else:
        st.dataframe(
            [
                {
                    "method": f.method.value,
                    "level": f"{f.level:.0%}",
                    "VaR (TRY)": num(f.var),
                    "ES (TRY)": num(f.es),
                    "VaR % of value": f"{f.var / r.value:.2%}" if r.value else "—",
                }
                for f in r.figures
            ],
            hide_index=True,
            width="stretch",
        )
        hist = r.figure(Method.HISTORICAL, 0.99)
        charts.show(
            charts.histogram(
                f"Daily P&L scenarios (TRY), {r.source}",
                [float(p) for _, p in r.scenarios],
                float(hist.var) if hist else None,
                "99% VaR",
            )
        )
    for x in r.excluded:
        st.caption(f"Left out: {x}")


left, right = st.columns(2)
with left:
    if report.portfolio is not None:
        var_block(report.portfolio)
with right:
    var_block(report.simulation)
if report.volatility is not None:
    st.caption(f"Realized volatility (annualized): {report.volatility:.2%}")

e = report.exposure
st.subheader(f"Exposure (total {num(e.total_try)} TRY)")
a, b, c = st.columns(3)
for col, title, rows in (
    (a, "By currency", e.by_currency),
    (b, "By asset class", e.by_asset_class),
    (c, "Top holdings", e.by_holding[:10]),
):
    with col:
        if rows:
            charts.show(
                charts.hbars(title, [x.name for x in rows], [float(x.weight) for x in rows])
            )
if e.effective_holdings is not None:
    st.caption(
        f"Concentration: HHI {e.herfindahl:.4f} ≈ {e.effective_holdings:.1f} equal-weight holdings"
    )
with st.expander("Exposure tables"):
    for title, rows in (("Account", e.by_account), ("Holding", e.by_holding)):
        st.dataframe(
            [
                {title.lower(): x.name, "TRY": num(x.value_try), "weight": f"{x.weight:.2%}"}
                for x in rows
            ],
            hide_index=True,
        )

m = report.correlation
off_diagonal = [v for i, row in enumerate(m.values) for v in row[i + 1 :] if v == v]
if len(m.names) > 1 and off_diagonal:
    charts.show(
        charts.diverging_heatmap(
            "Correlation of daily TRY returns",
            m.names,
            m.names,
            [[None if v != v else v for v in row] for row in m.values],
            limit=1.0,
            fmt=".2f",
        )
    )
elif len(m.names) > 1:
    st.caption("Correlation: not enough shared daily returns (20 needed per pair).")
for note in report.notes:
    st.caption(note)
if report.holdings.warnings:
    with st.expander(f"Warnings ({len(report.holdings.warnings)})"):
        for w in report.holdings.warnings:
            st.write(f"- {w}")
